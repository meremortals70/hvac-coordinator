"""The coordinator.

Reads Home Assistant state, hands it to the pure evaluator in `modes.py`, and
publishes the resulting traces. Every decision is made in the pure modules; this
file gathers inputs and manages lifecycle only.

Actuation itself lives in `actuator.py`. This file gathers inputs, runs the
evaluator and hands the resulting decision on.
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

from .actuator import Actuator, mean_cover_position, supported_hvac_modes
from .const import (
    COAST_HORIZON_HOURS,
    CONF_BAND_HIGH,
    CONF_BAND_LOW,
    CONF_BANDS,
    CONF_CLIMATE_ENTITY,
    CONF_COASTING_PERMITTED,
    CONF_CONSTRAINTS,
    CONF_COVER_ENTITIES,
    CONF_DIRECT_SUN_ENTITY,
    CONF_END,
    CONF_HUMIDITY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LOCKOUT_REASON,
    CONF_OPENING_ENTITIES,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
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
    PRECOOL_DEMAND_MARGIN_C,
)
from .forecast import (
    DEFAULT_HORIZON_HOURS,
    DemandForecast,
    RoomForecastInput,
    build_forecast,
)
from .hci import ComfortBand, dry_bulb_for_index
from .models import DecisionTrace, Mode, RoomConfig, RoomInputs
from .modes import evaluate_room
from .store import ModelStore
from .tariff import (
    CONSTRAINT_NO_GRID_IMPORT,
    CONSTRAINT_PRECOOL_OPPORTUNITY,
    TariffSchedule,
    TariffWindow,
)
from .thermal import Observation, ThermalModel

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
        self.actuator = Actuator(hass, self)
        self.outdoor_entity_id: str | None = config_entry.options.get(
            CONF_OUTDOOR_TEMPERATURE_ENTITY,
            config_entry.data.get(CONF_OUTDOOR_TEMPERATURE_ENTITY),
        )
        #: Learned thermal behaviour, one per room, restored from the store.
        self.models: dict[str, ThermalModel] = {}
        #: The last reading of each room, so the next evaluation can measure
        #: what actually happened over the interval and learn from it.
        self._previous: dict[str, tuple[datetime, float, float, int, bool]] = {}
        #: The published demand forecast. Never contains vendor concepts.
        self.forecast: DemandForecast | None = None
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
        self._load_models()
        self.previous_rooms = self._async_rooms_in_registry()

        # The climate entities are watched too: their capabilities and
        # availability feed the decision, not just their state.
        if watched := sorted(_watched_entities(self.rooms)):
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, watched, self._handle_state_change
                )
            )

    def _load_models(self) -> None:
        """Restore each room's learned behaviour from the store."""
        for room_id in self.rooms:
            self.models[room_id] = ThermalModel.from_dict(
                self.store.room(room_id).get("thermal")
            )

    def model_for(self, room_id: str) -> ThermalModel:
        """The thermal model for a room, created on first use."""
        if room_id not in self.models:
            self.models[room_id] = ThermalModel.from_dict(
                self.store.room(room_id).get("thermal")
            )
        return self.models[room_id]

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
        """Re-evaluate when a watched entity changes.

        Actuation is awaited, so this schedules a refresh rather than
        evaluating inline.
        """
        self.config_entry.async_create_task(
            self.hass, self.async_request_refresh(), "hvac_coordinator state change"
        )

    async def _async_update_data(self) -> dict[str, DecisionTrace]:
        """Evaluate every room."""
        return await self._async_evaluate(dt_util.utcnow())

    async def _async_evaluate(self, now: datetime) -> dict[str, DecisionTrace]:
        """Learn from the last interval, evaluate every room, then act."""
        traces: dict[str, DecisionTrace] = {}
        for room in self.rooms.values():
            inputs = self._inputs_for(room, now)
            self._learn(room, inputs, now)
            trace = evaluate_room(room, inputs)
            trace.model = self.model_for(room.room_id).diagnostics()
            traces[room.room_id] = trace
            LOGGER.debug(
                "%s: mode=%s actuator=%s hci=%s target=%s",
                room.room_id,
                trace.mode,
                trace.actuator,
                trace.hci,
                trace.target_dry_bulb_c,
            )
            await self.actuator.async_apply(room, trace)

        self._async_remove_stale_devices(set(traces))
        self.forecast = self._build_forecast(now, traces)
        self._persist_models()
        return traces

    def _learn(self, room: RoomConfig, inputs: RoomInputs, now: datetime) -> None:
        """Fold the interval since the last evaluation into the room's model.

        What the room actually did is measured at both ends; nothing here is
        inferred from what was commanded.
        """
        previous = self._previous.get(room.room_id)
        current = (
            now,
            inputs.temperature_c,
            inputs.relative_humidity,
            self._compressor_direction(room),
            self._is_drying(room),
        )
        if inputs.temperature_c is None or inputs.relative_humidity is None:
            # No reading at this end of the interval. Drop the anchor rather
            # than learning from a gap.
            self._previous.pop(room.room_id, None)
            return

        self._previous[room.room_id] = current  # type: ignore[assignment]
        if previous is None:
            return

        started, start_c, start_rh, compressor, drying = previous
        if start_c is None or start_rh is None:
            return

        elapsed = (now - started).total_seconds() / 3600.0
        self.model_for(room.room_id).observe(
            Observation(
                elapsed_hours=elapsed,
                indoor_start_c=start_c,
                indoor_end_c=inputs.temperature_c,
                humidity_start=start_rh,
                humidity_end=inputs.relative_humidity,
                outdoor_c=self._number(self.outdoor_entity_id),
                direct_sun=inputs.direct_sun is True,
                compressor=compressor,
                drying=drying,
            )
        )

    def _predicted_to_hold(self, room: RoomConfig) -> bool | None:
        """Whether the room stays in band unaided over the coast horizon.

        None means the model cannot say, which the evaluator treats as "do not
        coast" rather than as "yes". That is the hysteresis fallback: until the
        filter has converged, the band is simply held.
        """
        model = self.model_for(room.room_id)
        indoor = self._number(room.temperature_entity_id)
        humidity = self._number(room.humidity_entity_id)
        if indoor is None or humidity is None:
            return None

        band = room.band_for(Mode.SLEEP if self._sleeping(room) else Mode.OCCUPIED)
        if band is None:
            return None

        # The band is in comfort index; the model works in dry bulb. Convert
        # the bounds at the current humidity so the two are comparable.
        lower_c = dry_bulb_for_index(band.low, humidity)
        upper_c = dry_bulb_for_index(band.high, humidity)

        return model.holds_through(
            indoor,
            self._number(self.outdoor_entity_id),
            direct_sun=self._direct_sun(room) is True,
            hours=COAST_HORIZON_HOURS,
            lower_c=lower_c,
            upper_c=upper_c,
        )

    def _demand_ahead(self, room: RoomConfig) -> bool:
        """Whether this room is forecast to need cooling later today.

        Precool banks thermal mass against a load that is coming. Without a
        load coming it is just spending energy early, so this gates it.
        """
        model = self.model_for(room.room_id)
        indoor = self._number(room.temperature_entity_id)
        outdoor = self._number(self.outdoor_entity_id)
        if indoor is None or outdoor is None:
            return False

        drift = model.drift_rate(
            indoor, outdoor, direct_sun=self._direct_sun(room) is True
        )
        if drift is None:
            # Not learned yet. Outdoor above indoor is the honest fallback: the
            # room will warm, even if we cannot say how fast.
            return outdoor > indoor + PRECOOL_DEMAND_MARGIN_C
        return drift > 0

    def _sleeping(self, room: RoomConfig) -> bool:
        """Whether the room's sleep schedule is currently on."""
        return self._bool(room.sleep_schedule_entity_id) is True

    def _compressor_direction(self, room: RoomConfig) -> int:
        """Whether the unit is moving sensible heat, and which way."""
        state = self.hass.states.get(room.climate_entity_id)
        if state is None:
            return 0
        if state.state == "cool":
            return -1
        if state.state == "heat":
            return 1
        if state.state in ("heat_cool", "auto"):
            # Direction is whatever the unit decided. hvac_action says which.
            action = state.attributes.get("hvac_action")
            if action == "cooling":
                return -1
            if action == "heating":
                return 1
        return 0

    def _is_drying(self, room: RoomConfig) -> bool:
        """Whether the unit is in dry mode."""
        state = self.hass.states.get(room.climate_entity_id)
        return state is not None and state.state == "dry"

    def _persist_models(self) -> None:
        """Write learned state back to the store, on its own delay."""
        for room_id, model in self.models.items():
            record = dict(self.store.room(room_id))
            record["thermal"] = model.as_dict()
            self.store.update_room(room_id, record)

    def _build_forecast(
        self, now: datetime, traces: dict[str, DecisionTrace]
    ) -> DemandForecast:
        """Project HVAC energy over the horizon. No vendor concepts in it."""
        outdoor = self._number(self.outdoor_entity_id)
        inputs: list[RoomForecastInput] = []
        for room_id, room in self.rooms.items():
            trace = traces.get(room_id)
            inputs.append(
                RoomForecastInput(
                    room_id=room_id,
                    model=self.model_for(room_id),
                    indoor_c=self._number(room.temperature_entity_id),
                    target_c=trace.target_dry_bulb_c if trace else None,
                    outdoor_c=outdoor,
                    direct_sun=self._direct_sun(room) is True,
                    will_run=trace is not None
                    and trace.mode not in (Mode.LOCKOUT, Mode.UNOCCUPIED),
                )
            )

        windows: list[tuple[time, time, str, frozenset[str]]] = []
        if self.tariff is not None:
            windows = [
                (window.start, window.end, window.rate, window.constraints)
                for window in self.tariff.windows
            ]

        return build_forecast(
            dt_util.as_local(now), inputs, windows, DEFAULT_HORIZON_HOURS
        )

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
                self.actuator.forget(room_id)
            self.models.pop(room_id, None)
            self._previous.pop(room_id, None)
            self.store.forget_room(room_id)
            if device := registry.async_get_device(identifiers={(DOMAIN, room_id)}):
                    registry.async_update_device(
                        device_id=device.id,
                        remove_config_entry_id=self.config_entry.entry_id,
                    )
        self.previous_rooms = current

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
            direct_sun=self._direct_sun(room),
            has_covers=bool(room.cover_entity_ids),
            cover_position=mean_cover_position(self.hass, room.cover_entity_ids),
            **self._capabilities(room),
            opening_open=any(
                self._bool(entity_id) is True for entity_id in room.opening_entity_ids
            ),
            precool_opportunity=CONSTRAINT_PRECOOL_OPPORTUNITY in constraints,
            no_grid_import=CONSTRAINT_NO_GRID_IMPORT in constraints,
            coasting_permitted=window.coasting_permitted if window else True,
            heading_home=room.room_id in self._heading_home,
            precondition_deadline=self._heading_home.get(room.room_id),
            predicted_to_hold=self._predicted_to_hold(room),
            forecast_demand_ahead=self._demand_ahead(room),
            sleep_schedule_active=self._bool(room.sleep_schedule_entity_id)
            is True,
        )

    def _direct_sun(self, room: RoomConfig) -> bool | None:
        """Whether the sun is on this room's windows.

        Prefers a per-room sensor, because it is geometry: sun position against
        the window aspect. Adaptive Cover Pro publishes one per cover.

        Indoor light level is deliberately not used. A semi-transparent blind
        reads bright when it is fully closed, so lux would report nothing to
        block at exactly the moment the blind is already blocking.

        With no sensor configured, falls back to whether the sun is above the
        horizon — true of the whole house, so it will be wrong for a room the
        sun never reaches. Configure the sensor.
        """
        if room.direct_sun_entity_id:
            return self._bool(room.direct_sun_entity_id)
        sun = self.hass.states.get("sun.sun")
        if sun is None:
            return None
        return sun.state == "above_horizon"

    def _capabilities(self, room: RoomConfig) -> dict[str, bool]:
        """What the unit can do, so the decision never picks an absent mode.

        A missing entity reports everything as unavailable, which stops the
        selector choosing a step that could not be carried out.
        """
        state = self.hass.states.get(room.climate_entity_id)
        if state is None:
            return {
                "can_cool": False,
                "can_heat": False,
                "can_dry": False,
                "can_fan_only": False,
            }
        modes = supported_hvac_modes(state)
        return {
            "can_cool": bool(modes & {"cool", "heat_cool", "auto"}),
            "can_heat": bool(modes & {"heat", "heat_cool", "auto"}),
            "can_dry": "dry" in modes,
            "can_fan_only": "fan_only" in modes,
        }

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
                room.direct_sun_entity_id,
                room.sleep_schedule_entity_id,
            )
            if entity_id
        )
        watched.add(room.climate_entity_id)
        watched.update(room.opening_entity_ids)
        watched.update(room.cover_entity_ids)
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
        direct_sun_entity_id=raw.get(CONF_DIRECT_SUN_ENTITY),
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
