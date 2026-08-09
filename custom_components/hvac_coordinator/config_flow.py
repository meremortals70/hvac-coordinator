"""Config, options and reconfigure flows.

Only what a user cannot get a correct result without is exposed. Regulation
thresholds, model parameters and filter tuning are not settings.

Nothing is seeded. A fresh install has no rooms, no bands and no tariff, and
does nothing until they are entered.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import selector

from .const import (
    CONF_BAND_HIGH,
    CONF_BAND_LOW,
    CONF_BANDS,
    CONF_CLIMATE_ENTITY,
    CONF_COVER_ENTITIES,
    CONF_HUMIDITY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_LOCKOUT_REASON,
    CONF_OPENING_ENTITIES,
    CONF_PRESENCE_ENTITY,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SLEEP_SCHEDULE_ENTITY,
    CONF_TARIFF_WINDOWS,
    CONF_TEMPERATURE_ENTITY,
    DOMAIN,
)
from .models import Mode

ROOM_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        vol.Required(CONF_CLIMATE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="climate")
        ),
        vol.Optional(CONF_TEMPERATURE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Optional(CONF_HUMIDITY_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
        ),
        vol.Optional(CONF_PRESENCE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Optional(CONF_SLEEP_SCHEDULE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["schedule", "input_boolean", "binary_sensor"]
            )
        ),
        vol.Optional(CONF_ILLUMINANCE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="illuminance")
        ),
        vol.Optional(CONF_OPENING_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
        ),
        vol.Optional(CONF_COVER_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="cover", multiple=True)
        ),
        vol.Optional(CONF_LOCKOUT_REASON): selector.TextSelector(),
    }
)

_BAND_MODES = (Mode.OCCUPIED, Mode.SLEEP, Mode.PRECOOL)

BANDS_SCHEMA = vol.Schema(
    {
        vol.Optional(f"{mode}_{bound}"): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=50, step=0.5, mode=selector.NumberSelectorMode.BOX
            )
        )
        for mode in _BAND_MODES
        for bound in (CONF_BAND_LOW, CONF_BAND_HIGH)
    }
)


def _slug(name: str) -> str:
    """Derive a stable room id from the room name."""
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")


def _bands_from_input(user_input: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Collect only the bands where both bounds were supplied."""
    bands: dict[str, dict[str, float]] = {}
    for mode in _BAND_MODES:
        low = user_input.get(f"{mode}_{CONF_BAND_LOW}")
        high = user_input.get(f"{mode}_{CONF_BAND_HIGH}")
        if low is not None and high is not None:
            bands[str(mode)] = {CONF_BAND_LOW: float(low), CONF_BAND_HIGH: float(high)}
    return bands


class HvacCoordinatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry. Rooms and tariff are added afterwards."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
        return self.async_create_entry(
            title="HVAC Coordinator",
            data={CONF_ROOMS: [], CONF_TARIFF_WINDOWS: []},
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: Any,
    ) -> HvacCoordinatorOptionsFlow:
        """Return the options flow."""
        return HvacCoordinatorOptionsFlow()


class HvacCoordinatorOptionsFlow(OptionsFlow):
    """Add, edit and remove rooms."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._room: dict[str, Any] = {}
        self._editing: str | None = None

    @property
    def _rooms(self) -> list[dict[str, Any]]:
        """Rooms as currently configured."""
        return list(
            self.config_entry.options.get(
                CONF_ROOMS, self.config_entry.data.get(CONF_ROOMS, [])
            )
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer what can be done, rather than assuming a room is being added."""
        if not self._rooms:
            # Nothing configured yet: skip the menu and go straight to adding.
            return await self.async_step_room()
        return self.async_show_menu(
            step_id="init", menu_options=["room", "edit_room", "remove_room"]
        )

    async def async_step_edit_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a room to edit, then reuse the add steps with it prefilled."""
        if user_input is None:
            return self.async_show_form(
                step_id="edit_room", data_schema=self._room_choice_schema()
            )
        self._editing = user_input[CONF_ROOM_ID]
        return await self.async_step_room()

    async def async_step_remove_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a room. Its device and entities go with it."""
        if user_input is None:
            return self.async_show_form(
                step_id="remove_room", data_schema=self._room_choice_schema()
            )
        remaining = [
            room
            for room in self._rooms
            if room[CONF_ROOM_ID] != user_input[CONF_ROOM_ID]
        ]
        options = dict(self.config_entry.options)
        options[CONF_ROOMS] = remaining
        return self.async_create_entry(title="", data=options)

    def _room_choice_schema(self) -> vol.Schema:
        """A picker over the configured rooms."""
        return vol.Schema(
            {
                vol.Required(CONF_ROOM_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=room[CONF_ROOM_ID], label=room["name"]
                            )
                            for room in self._rooms
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    def _existing(self) -> dict[str, Any]:
        """The room being edited, or an empty dict when adding."""
        if self._editing is None:
            return {}
        return next(
            (room for room in self._rooms if room[CONF_ROOM_ID] == self._editing),
            {},
        )

    async def async_step_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the room's entities."""
        if user_input is None:
            existing = self._existing()
            return self.async_show_form(
                step_id="room",
                data_schema=self.add_suggested_values_to_schema(
                    ROOM_SCHEMA, existing
                )
                if existing
                else ROOM_SCHEMA,
            )

        self._room = {
            CONF_ROOM_ID: _slug(user_input["name"]),
            "name": user_input["name"],
            CONF_CLIMATE_ENTITY: user_input[CONF_CLIMATE_ENTITY],
            CONF_TEMPERATURE_ENTITY: user_input.get(CONF_TEMPERATURE_ENTITY),
            CONF_HUMIDITY_ENTITY: user_input.get(CONF_HUMIDITY_ENTITY),
            CONF_PRESENCE_ENTITY: user_input.get(CONF_PRESENCE_ENTITY),
            CONF_SLEEP_SCHEDULE_ENTITY: user_input.get(CONF_SLEEP_SCHEDULE_ENTITY),
            CONF_ILLUMINANCE_ENTITY: user_input.get(CONF_ILLUMINANCE_ENTITY),
            CONF_OPENING_ENTITIES: user_input.get(CONF_OPENING_ENTITIES, []),
            CONF_COVER_ENTITIES: user_input.get(CONF_COVER_ENTITIES, []),
            CONF_LOCKOUT_REASON: user_input.get(CONF_LOCKOUT_REASON),
        }
        return await self.async_step_bands()

    async def async_step_bands(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the comfort bands for this room."""
        errors: dict[str, str] = {}
        if user_input is None:
            existing_bands = self._existing().get(CONF_BANDS, {})
            suggested = {
                f"{mode}_{bound}": values[bound]
                for mode, values in existing_bands.items()
                for bound in (CONF_BAND_LOW, CONF_BAND_HIGH)
                if bound in values
            }
            return self.async_show_form(
                step_id="bands",
                data_schema=self.add_suggested_values_to_schema(
                    BANDS_SCHEMA, suggested
                )
                if suggested
                else BANDS_SCHEMA,
            )

        if user_input is not None:
            bands = _bands_from_input(user_input)
            if any(
                values[CONF_BAND_LOW] >= values[CONF_BAND_HIGH]
                for values in bands.values()
            ):
                errors["base"] = "band_inverted"
            else:
                self._room[CONF_BANDS] = bands
                return self._save_room()

        return self.async_show_form(
            step_id="bands", data_schema=BANDS_SCHEMA, errors=errors
        )

    def _save_room(self) -> ConfigFlowResult:
        """Add or replace the room in the entry options."""
        options = dict(self.config_entry.options)
        # Replace the room being edited, and any room whose name produces the
        # same id, so editing a name does not leave the old room behind.
        replaced = {self._room[CONF_ROOM_ID], self._editing}
        rooms: list[dict[str, Any]] = [
            room for room in self._rooms if room[CONF_ROOM_ID] not in replaced
        ]
        rooms.append(self._room)
        options[CONF_ROOMS] = rooms
        options.setdefault(
            CONF_TARIFF_WINDOWS,
            self.config_entry.data.get(CONF_TARIFF_WINDOWS, []),
        )
        return self.async_create_entry(title="", data=options)
