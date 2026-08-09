"""The coordinator.

Reads Home Assistant state, hands it to the pure evaluator in `modes.py`, and
publishes the resulting traces. Every decision is made in the pure modules; this
file gathers inputs and manages lifecycle only.

NOT WIRED — actuation.
The evaluator returns which actuator step is correct. Carrying it out means
calling Versatile Thermostat and Adaptive Cover Pro services, whose schemas have
not been read from source. `_async_apply` logs the intent and calls nothing.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BAND_HIGH,
    CONF_BAND_LOW,
    CONF_BANDS,
    CONF_CLIMATE_ENTITY,
    CONF_COASTING_PERMITTED,
    CONF_CONSTRAINTS,
    CONF_COVER_ENTITIES,
    CONF_END,
    CONF_HUMIDITY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LOCKOUT_REASON,
    CONF_OPENING_ENTITIES,
    CONF_PRESENCE_ENTITY,
    CONF_RATE,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SLEEP_SCHEDULE_ENTITY,
    CONF_START,
    CONF_TARIFF_WINDOWS,
    CONF_TEMPERATURE_ENTITY,
    DOMAIN,
    EVALUATION_INTERVAL,
    ISSUE_NO_BANDS,
    ISSUE_UNRECOGNISED_CONSTRAINT,
    LOGGER,
)
from .hci import ComfortBand
from .models import DecisionTrace, Mode, RoomConfig, RoomInputs
from .modes import evaluate_room
from .store import ModelStore
from .tariff import (
    CONSTRAINT_NO_GRID_IMPORT,
    CONSTRAINT_PRECOOL_OPPORTUNITY,
    TariffSchedule,
    TariffWindow,
)

if TYPE_CHECKING:
    from . import HvacConfigEntry

#: State strings that carry no reading, as distinct from a number.
_NON_NUMERIC = frozenset({"unknown", "unavailable", "none", ""})


class HvacCoordinator(DataUpdateCoordinator[dict[str, DecisionTrace]]):
    """Evaluates every room and publishes its decision trace."""

    config_entry: HvacConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HvacConfigEntry,
        store: ModelStore,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=EVALUATION_INTERVAL,
        )
        self.store = store
        self.rooms: dict[str, RoomConfig] = _rooms_from_entry(config_entry)
        self.tariff: TariffSchedule | None = _tariff_from_entry(config_entry)
        #: Rooms that have a device in the registry, for stale removal. Seeded
        #: from the registry rather than from the previous evaluation: the
        #: coordinator is rebuilt on every options change, so anything held
        #: only in memory would forget the room that was just deleted and its
        #: device would be orphaned.
        self.previous_rooms: set[str] = set()
        #: Heading-home requests, per room, with the deadline if one was given.
        #: There is no target: a heading-home room is driven to its comfort band.
        self._heading_home: dict[str, datetime | None] = {}

    async def async_prepare(self) -> None:
        """Subscribe to the entities every room reads, and raise any issues."""
        self._async_check_configuration()
        self.previous_rooms = self._async_rooms_in_registry()

        if watched := sorted(_watched_entities(self.rooms)):
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, watched, self._handle_state_change
                )
            )

    @callback
    def _async_check_configuration(self) -> None:
        """Surface configuration problems as repair issues, not log noise."""
        unrecognised = (
            self.tariff.unrecognised_constraints() if self.tariff else frozenset()
        )
        if unrecognised:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_UNRECOGNISED_CONSTRAINT,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_UNRECOGNISED_CONSTRAINT,
                translation_placeholders={
                    "constraints": ", ".join(sorted(unrecognised))
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_UNRECOGNISED_CONSTRAINT)

        unbanded = sorted(
            room.name
            for room in self.rooms.values()
            if not room.bands and room.lockout_reason is None
        )
        if unbanded:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_NO_BANDS,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NO_BANDS,
                translation_placeholders={"rooms": ", ".join(unbanded)},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_NO_BANDS)

    @callback
    def async_request_heading_home(
        self, room_id: str, deadline: datetime | None = None
    ) -> None:
        """Bring a room to its comfort band ahead of arrival."""
        self._heading_home[room_id] = deadline

    @callback
    def async_clear_override(self, room_id: str) -> None:
        """Drop any heading-home request for a room."""
        self._heading_home.pop(room_id, None)

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Re-evaluate when a watched entity changes."""
        self.async_set_updated_data(self._evaluate(dt_util.utcnow()))

    async def _async_update_data(self) -> dict[str, DecisionTrace]:
        """Evaluate every room."""
        return self._evaluate(dt_util.utcnow())

    @callback
    def _evaluate(self, now: datetime) -> dict[str, DecisionTrace]:
        traces: dict[str, DecisionTrace] = {}
        for room in self.rooms.values():
            trace = evaluate_room(room, self._inputs_for(room, now))
            traces[room.room_id] = trace
            self._async_apply(room, trace)

        self._async_remove_stale_devices(set(traces))
        return traces

    @callback
    def _async_rooms_in_registry(self) -> set[str]:
        """Room ids that currently have a device registered to this entry."""
        registry = dr.async_get(self.hass)
        return {
            identifier[1]
            for device in dr.async_entries_for_config_entry(
                registry, self.config_entry.entry_id
            )
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
        }

    @callback
    def _async_remove_stale_devices(self, current: set[str]) -> None:
        """Drop devices for rooms that are no longer configured."""
        if stale := self.previous_rooms - current:
            registry = dr.async_get(self.hass)
            for room_id in stale:
                if device := registry.async_get_device(identifiers={(DOMAIN, room_id)}):
                    registry.async_update_device(
                        device_id=device.id,
                        remove_config_entry_id=self.config_entry.entry_id,
                    )
        self.previous_rooms = current

    @callback
    def _async_apply(self, room: RoomConfig, trace: DecisionTrace) -> None:
        """Carry out the decision.

        Deliberately logs only. See the module docstring.
        """
        LOGGER.debug(
            "%s: mode=%s actuator=%s hci=%s target=%s reasons=%s",
            room.room_id,
            trace.mode,
            trace.actuator,
            trace.hci,
            trace.target_dry_bulb_c,
            "; ".join(trace.reasons),
        )

    def _inputs_for(self, room: RoomConfig, now: datetime) -> RoomInputs:
        """Assemble everything the evaluator is allowed to see."""
        window = self._window_at(dt_util.as_local(now).time())
        constraints = window.constraints if window else frozenset()

        return RoomInputs(
            now=now,
            temperature_c=self._number(room.temperature_entity_id),
            relative_humidity=self._number(room.humidity_entity_id),
            presence=self._bool(room.presence_entity_id),
            illuminance_lux=self._number(room.illuminance_entity_id),
            has_covers=bool(room.cover_entity_ids),
            opening_open=any(
                self._bool(entity_id) is True for entity_id in room.opening_entity_ids
            ),
            precool_opportunity=CONSTRAINT_PRECOOL_OPPORTUNITY in constraints,
            no_grid_import=CONSTRAINT_NO_GRID_IMPORT in constraints,
            coasting_permitted=window.coasting_permitted if window else True,
            heading_home=room.room_id in self._heading_home,
            precondition_deadline=self._heading_home.get(room.room_id),
            # None until the thermal model exists and has converged. COAST is
            # never entered before then: the hysteresis fallback holds the band.
            predicted_to_hold=None,
            # Set once the demand forecast exists.
            forecast_demand_ahead=False,
            sleep_schedule_active=self._bool(room.sleep_schedule_entity_id)
            is True,
        )

    def _window_at(self, at: time) -> TariffWindow | None:
        """The tariff window in force, or None if no schedule is configured."""
        if self.tariff is None:
            return None
        try:
            return self.tariff.window_at(at)
        except ValueError:
            LOGGER.warning("No tariff window covers %s", at)
            return None

    @callback
    def _note_availability(self, entity_id: str, available: bool) -> None:
        """Log each availability transition once, not once per evaluation."""
        if available:
            if entity_id in self._unavailable:
                self._unavailable.discard(entity_id)
                LOGGER.info("%s is available again", entity_id)
            return
        if entity_id not in self._unavailable:
            self._unavailable.add(entity_id)
            LOGGER.warning(
                "%s is unavailable; rooms depending on it will hold or stop "
                "actuating until it returns",
                entity_id,
            )

    def _number(self, entity_id: str | None) -> float | None:
        """A numeric reading, or None where there is none."""
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state.lower() in _NON_NUMERIC:
            self._note_availability(entity_id, available=False)
            return None
        try:
            value = float(state.state)
        except ValueError:
            LOGGER.warning(
                "%s reported a non-numeric value: %s", entity_id, state.state
            )
            self._note_availability(entity_id, available=False)
            return None
        self._note_availability(entity_id, available=True)
        return value

    def _bool(self, entity_id: str | None) -> bool | None:
        """An on/off reading, or None where there is none."""
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state.lower() in _NON_NUMERIC:
            self._note_availability(entity_id, available=False)
            return None
        self._note_availability(entity_id, available=True)
        return state.state == "on"


def _watched_entities(rooms: dict[str, RoomConfig]) -> set[str]:
    """Every entity the coordinator reads, across all rooms."""
    watched: set[str] = set()
    for room in rooms.values():
        watched.update(
            entity_id
            for entity_id in (
                room.temperature_entity_id,
                room.humidity_entity_id,
                room.presence_entity_id,
                room.illuminance_entity_id,
                room.sleep_schedule_entity_id,
            )
            if entity_id
        )
        watched.update(room.opening_entity_ids)
    return watched


def _rooms_from_entry(entry: HvacConfigEntry) -> dict[str, RoomConfig]:
    """Build room objects from configuration.

    Bad configuration raises ConfigEntryError, which Home Assistant shows on
    the integration page. Letting a KeyError or a ValueError escape here would
    surface as an unhandled traceback in the log and tell the user nothing.
    """
    rooms: dict[str, RoomConfig] = {}
    raw_rooms: list[dict[str, Any]] = entry.options.get(
        CONF_ROOMS, entry.data.get(CONF_ROOMS, [])
    )
    for raw in raw_rooms:
        try:
            bands = {
                Mode(name): ComfortBand(
                    low=values[CONF_BAND_LOW], high=values[CONF_BAND_HIGH]
                )
                for name, values in raw.get(CONF_BANDS, {}).items()
            }
        except (KeyError, TypeError, ValueError) as err:
            raise ConfigEntryError(
                f"Comfort bands for room {raw.get(CONF_ROOM_ID, '?')} are "
                f"invalid: {err}"
            ) from err

        try:
            room = _room_from_raw(raw, bands)
        except KeyError as err:
            raise ConfigEntryError(
                f"Room configuration is missing {err}"
            ) from err
        rooms[room.room_id] = room
    return rooms


def _room_from_raw(
    raw: dict[str, Any], bands: dict[Mode, ComfortBand]
) -> RoomConfig:
    """Build one room. Raises KeyError if a required field is absent."""
    return RoomConfig(
        room_id=raw[CONF_ROOM_ID],
        name=raw["name"],
        climate_entity_id=raw[CONF_CLIMATE_ENTITY],
        bands=bands,
        temperature_entity_id=raw.get(CONF_TEMPERATURE_ENTITY),
        humidity_entity_id=raw.get(CONF_HUMIDITY_ENTITY),
        presence_entity_id=raw.get(CONF_PRESENCE_ENTITY),
        sleep_schedule_entity_id=raw.get(CONF_SLEEP_SCHEDULE_ENTITY),
        illuminance_entity_id=raw.get(CONF_ILLUMINANCE_ENTITY),
        opening_entity_ids=tuple(raw.get(CONF_OPENING_ENTITIES, []) or []),
        cover_entity_ids=tuple(raw.get(CONF_COVER_ENTITIES, []) or []),
        lockout_reason=raw.get(CONF_LOCKOUT_REASON),
    )


def _tariff_from_entry(entry: HvacConfigEntry) -> TariffSchedule | None:
    """Build the tariff schedule from configuration, or None if unconfigured."""
    raw_windows: list[dict[str, Any]] = entry.options.get(
        CONF_TARIFF_WINDOWS, entry.data.get(CONF_TARIFF_WINDOWS, [])
    )
    if not raw_windows:
        return None
    try:
        windows = tuple(
            TariffWindow(
                start=time.fromisoformat(raw[CONF_START]),
                end=time.fromisoformat(raw[CONF_END]),
                rate=raw[CONF_RATE],
                constraints=frozenset(raw.get(CONF_CONSTRAINTS, []) or []),
                coasting_permitted=raw.get(CONF_COASTING_PERMITTED, True),
            )
            for raw in raw_windows
        )
        return TariffSchedule(windows)
    except (KeyError, TypeError, ValueError) as err:
        # A broken tariff must not take the whole integration down. Rooms still
        # hold their comfort bands; only the window-driven behaviour is lost.
        LOGGER.error("Tariff schedule is invalid and has been ignored: %s", err)
        return None
