"""Config, options and reconfigure flows.

Only what a user cannot get a correct result without is exposed. Regulation
thresholds, model parameters and filter tuning are not settings.

The initial setup collects the first room, so the integration does something
the moment it is added; further rooms, edits and removals go through the
options flow.

Comfort bands arrive seeded with defaults derived from the ASHRAE 55 comfort
zone, so a fresh install is sensible with no configuration. Nothing specific to
any house is seeded: entity IDs and tariff windows are always the user's own.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from .const import (
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_TARGETS,
    CONF_BAND_HIGH,
    CONF_BAND_LOW,
    CONF_BANDS,
    CONF_CLIMATE_ENTITY,
    CONF_COASTING_PERMITTED,
    CONF_CONSTRAINTS,
    CONF_COVER_ENTITIES,
    CONF_DAILY_SUPPLY_CENTS,
    CONF_DIRECT_SUN_ENTITY,
    CONF_END,
    CONF_EXPORT_CENTS,
    CONF_EXPORT_WINDOWS,
    CONF_HUMIDITY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_IMPORT_CENTS,
    CONF_LOCKOUT_REASON,
    CONF_LOCKOUT_REASONS,
    CONF_OCCUPIED_AFTER,
    CONF_OPENING_ENTITIES,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_OVERHANG_HEIGHT,
    CONF_OVERHANG_PROJECTION,
    CONF_PRESENCE_ENTITY,
    CONF_RATE,
    CONF_RATE_LABELS,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SLEEP_SCHEDULE_ENTITY,
    CONF_START,
    CONF_TARIFF_WINDOWS,
    CONF_TEMPERATURE_ENTITY,
    CONF_VACANT_AFTER,
    CONF_WARNING_GRACE,
    CONF_WINDOW_DIRECTION,
    DOMAIN,
    NOT_LOCKED_OUT,
)
from .forms import (
    BAND_MODES,
    bands_are_valid,
    bands_as_suggestions,
    bands_from_input,
    default_band_suggestions,
    default_grace_suggestions,
    describe_configuration,
    describe_export_window,
    describe_window,
    export_window_from_input,
    extend_lockout_reasons,
    extend_rate_labels,
    known_lockout_reasons,
    known_rate_labels,
    room_from_input,
    schedule_gaps,
    sort_windows,
    window_as_suggestions,
    window_from_input,
)
from .sun import WINDOW_DIRECTIONS
from .tariff import KNOWN_CONSTRAINTS

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
        vol.Optional(CONF_DIRECT_SUN_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Optional(CONF_OPENING_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
        ),
        vol.Optional(CONF_COVER_ENTITIES): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="cover", multiple=True)
        ),
    }
)


def _metres_selector() -> selector.NumberSelector:
    """A metres box, for the overhang measurements."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=10, step=0.05, unit_of_measurement="m",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _cents_selector() -> selector.NumberSelector:
    """A cents-per-unit box."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=1000, step=0.01, unit_of_measurement="c",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def export_window_schema() -> vol.Schema:
    """A feed-in rate, optionally limited to part of the day."""
    return vol.Schema(
        {
            vol.Required(CONF_EXPORT_CENTS): _cents_selector(),
            vol.Optional(CONF_START, default="00:00:00"): selector.TimeSelector(),
            vol.Optional(CONF_END, default="00:00:00"): selector.TimeSelector(),
        }
    )


def _minutes_selector() -> selector.NumberSelector:
    """A minutes box for the grace timings."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=120, step=1, unit_of_measurement="min",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def room_schema(lockout_reasons: list[str]) -> vol.Schema:
    """The room form, with the lockout dropdown built from known reasons.

    Lockout is one field, not a tick box and a second screen. The first option
    means the room is not locked out, so choosing a reason is the same action
    as switching lockout on, and the reason can never be set by accident
    because it is never a free text box.
    """
    return ROOM_SCHEMA.extend(
        {
            vol.Optional(CONF_OCCUPIED_AFTER): _minutes_selector(),
            vol.Optional(CONF_VACANT_AFTER): _minutes_selector(),
            vol.Optional(CONF_WARNING_GRACE): _minutes_selector(),
            vol.Optional(CONF_ANNOUNCE, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_ANNOUNCE_TARGETS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player", multiple=True)
            ),
            vol.Optional(CONF_OVERHANG_PROJECTION): _metres_selector(),
            vol.Optional(CONF_OVERHANG_HEIGHT): _metres_selector(),
            vol.Optional(CONF_WINDOW_DIRECTION): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(WINDOW_DIRECTIONS),
                    translation_key="window_direction",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_LOCKOUT_REASON, default=NOT_LOCKED_OUT
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=lockout_reasons,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


BANDS_SCHEMA = vol.Schema(
    {
        vol.Optional(f"{mode}_{bound}"): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=50, step=0.5, mode=selector.NumberSelectorMode.BOX
            )
        )
        for mode in BAND_MODES
        for bound in (CONF_BAND_LOW, CONF_BAND_HIGH)
    }
)


def window_schema(rate_labels: list[str]) -> vol.Schema:
    """One tariff window: when it runs, what it costs, what rules apply."""
    return vol.Schema(
        {
            vol.Required(CONF_START): selector.TimeSelector(),
            vol.Required(CONF_END): selector.TimeSelector(),
            vol.Optional(CONF_IMPORT_CENTS): _cents_selector(),
            vol.Required(CONF_RATE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=rate_labels,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    sort=True,
                )
            ),
            vol.Optional(CONF_CONSTRAINTS, default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sorted(KNOWN_CONSTRAINTS),
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_COASTING_PERMITTED, default=True
            ): selector.BooleanSelector(),
        }
    )


class _RoomSteps:
    """The room, lockout and bands steps, shared by both flows.

    Both flows collect a room the same way. The only difference is what happens
    to it afterwards, which is what _save_room does.
    """

    _room: dict[str, Any]

    def _known_lockout_reasons(self) -> list[str]:
        """Built-in reasons plus any the user has added, deduplicated."""
        return known_lockout_reasons(self._stored_lockout_reasons())

    def _stored_lockout_reasons(self) -> list[str]:
        """Reasons the user has typed before. Empty for a fresh install."""
        return []

    def _suggested_room(self) -> dict[str, Any]:
        """Values to prefill the room form with.

        A new room arrives with the default grace timings already in it, so it
        behaves sensibly without anyone reasoning about compressor cycling.
        """
        return dict(default_grace_suggestions())

    def _suggested_bands(self) -> dict[str, float]:
        """Values to prefill the bands form with.

        A new room gets the seeded defaults, so the form arrives with sensible
        numbers rather than six empty boxes.
        """
        return default_band_suggestions()

    async def async_step_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the room and the entities that describe it."""
        if user_input is None:
            schema = room_schema(known_lockout_reasons(self._stored_lockout_reasons()))
            return self.async_show_form(  # type: ignore[attr-defined,no-any-return]
                step_id="room",
                data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                    schema, self._suggested_room()
                ),
            )

        self._room = room_from_input(user_input)
        return await self.async_step_bands()

    async def async_step_bands(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the comfort bands for this room."""
        errors: dict[str, str] = {}

        if user_input is not None:
            bands = bands_from_input(user_input)
            if not bands_are_valid(bands):
                errors["base"] = "band_inverted"
            else:
                self._room[CONF_BANDS] = bands
                return self._save_room()

        suggested = self._suggested_bands()
        return self.async_show_form(  # type: ignore[attr-defined,no-any-return]
            step_id="bands",
            data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                BANDS_SCHEMA, suggested
            ),
            errors=errors,
        )

    def _save_room(self) -> ConfigFlowResult:
        """Store the collected room. Implemented by each flow."""
        raise NotImplementedError


class HvacCoordinatorConfigFlow(_RoomSteps, ConfigFlow, domain=DOMAIN):
    """Initial setup. Collects the first room, so setup produces something."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._room = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start by describing the first room."""
        return await self.async_step_room(user_input)

    def _save_room(self) -> ConfigFlowResult:
        """Create the entry with the first room in it."""
        return self.async_create_entry(
            title="Abode HVAC Coordinator",
            data={
                CONF_ROOMS: [self._room],
                CONF_TARIFF_WINDOWS: [],
                CONF_LOCKOUT_REASONS: extend_lockout_reasons([], self._room),
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> HvacCoordinatorOptionsFlow:
        """Return the options flow."""
        return HvacCoordinatorOptionsFlow()


class HvacCoordinatorOptionsFlow(_RoomSteps, OptionsFlow):
    """Add, edit and remove rooms."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._room = {}
        self._editing: str | None = None
        self._editing_window: str | None = None

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
            return await self.async_step_room()
        return self.async_show_menu(
            step_id="init",
            description_placeholders={"configuration": self._summary()},
            menu_options=[
                "room",
                "edit_room",
                "remove_room",
                "add_window",
                "edit_window",
                "remove_window",
                "export_rate",
                "supply_charge",
                "outdoor",
            ],
        )

    def _summary(self) -> str:
        """Everything currently configured, shown on the menu itself."""
        return describe_configuration(
            self._rooms,
            self._windows,
            self._export_windows,
            self.config_entry.options.get(
                CONF_DAILY_SUPPLY_CENTS,
                self.config_entry.data.get(CONF_DAILY_SUPPLY_CENTS),
            ),
            self.config_entry.options.get(
                CONF_OUTDOOR_TEMPERATURE_ENTITY,
                self.config_entry.data.get(CONF_OUTDOOR_TEMPERATURE_ENTITY),
            ),
        )

    async def async_step_edit_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a room to edit, then reuse the room steps with it prefilled."""
        if user_input is None:
            return self.async_show_form(
                step_id="edit_room",
                data_schema=self._room_choice_schema(),
                description_placeholders={"configuration": self._summary()},
            )
        self._editing = user_input[CONF_ROOM_ID]
        return await self.async_step_room()

    async def async_step_remove_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a room. Its device and entities go with it."""
        if user_input is None:
            return self.async_show_form(
                step_id="remove_room",
                data_schema=self._room_choice_schema(),
                description_placeholders={"configuration": self._summary()},
            )
        remaining = [
            room
            for room in self._rooms
            if room[CONF_ROOM_ID] != user_input[CONF_ROOM_ID]
        ]
        options = dict(self.config_entry.options)
        options[CONF_ROOMS] = remaining
        options.setdefault(
            CONF_TARIFF_WINDOWS, self.config_entry.data.get(CONF_TARIFF_WINDOWS, [])
        )
        return self.async_create_entry(title="", data=options)

    # ---- tariff -------------------------------------------------------

    @property
    def _windows(self) -> list[dict[str, Any]]:
        """Tariff windows as currently configured."""
        return list(
            self.config_entry.options.get(
                CONF_TARIFF_WINDOWS,
                self.config_entry.data.get(CONF_TARIFF_WINDOWS, []),
            )
        )

    def _stored_rate_labels(self) -> list[str]:
        return list(
            self.config_entry.options.get(
                CONF_RATE_LABELS, self.config_entry.data.get(CONF_RATE_LABELS, [])
            )
        )

    async def async_step_add_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one tariff window."""
        errors: dict[str, str] = {}
        schema = window_schema(known_rate_labels(self._stored_rate_labels()))

        if user_input is not None:
            window = window_from_input(user_input)
            if window[CONF_START] == window[CONF_END] and self._windows:
                errors["base"] = "window_whole_day"
            else:
                windows = sort_windows([*self._windows, window])
                options = dict(self.config_entry.options)
                options[CONF_TARIFF_WINDOWS] = windows
                options[CONF_RATE_LABELS] = extend_rate_labels(
                    self._stored_rate_labels(), window[CONF_RATE]
                )
                options.setdefault(CONF_ROOMS, self._rooms)
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="add_window",
            data_schema=schema,
            errors=errors,
            description_placeholders={"schedule": self._schedule_summary()},
        )

    async def async_step_outdoor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The outdoor temperature feed, used by the thermal model."""
        if user_input is None:
            current = self.config_entry.options.get(
                CONF_OUTDOOR_TEMPERATURE_ENTITY,
                self.config_entry.data.get(CONF_OUTDOOR_TEMPERATURE_ENTITY),
            )
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_OUTDOOR_TEMPERATURE_ENTITY
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="temperature"
                        )
                    )
                }
            )
            return self.async_show_form(
                step_id="outdoor",
                data_schema=self.add_suggested_values_to_schema(
                    schema, {CONF_OUTDOOR_TEMPERATURE_ENTITY: current}
                )
                if current
                else schema,
            )

        options = dict(self.config_entry.options)
        options[CONF_OUTDOOR_TEMPERATURE_ENTITY] = user_input.get(
            CONF_OUTDOOR_TEMPERATURE_ENTITY
        )
        options.setdefault(CONF_ROOMS, self._rooms)
        options.setdefault(CONF_TARIFF_WINDOWS, self._windows)
        return self.async_create_entry(title="", data=options)

    async def async_step_edit_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change an existing tariff window."""
        if not self._windows:
            return self.async_abort(reason="no_windows")

        if user_input is None:
            return self.async_show_form(
                step_id="edit_window",
                data_schema=self._window_choice_schema(),
                description_placeholders={"schedule": self._schedule_summary()},
            )

        if self._editing_window is None:
            self._editing_window = user_input[CONF_START]
            return await self.async_step_window_detail()
        return await self.async_step_window_detail(user_input)

    async def async_step_window_detail(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The window itself, prefilled with what it currently is."""
        existing = next(
            (
                window
                for window in self._windows
                if str(window[CONF_START]) == self._editing_window
            ),
            None,
        )
        if existing is None:
            return self.async_abort(reason="no_windows")

        schema = window_schema(known_rate_labels(self._stored_rate_labels()))
        if user_input is None:
            return self.async_show_form(
                step_id="window_detail",
                data_schema=self.add_suggested_values_to_schema(
                    schema, window_as_suggestions(existing)
                ),
            )

        replacement = window_from_input(user_input)
        remaining = [
            window
            for window in self._windows
            if str(window[CONF_START]) != self._editing_window
        ]
        options = dict(self.config_entry.options)
        options[CONF_TARIFF_WINDOWS] = sort_windows([*remaining, replacement])
        options[CONF_RATE_LABELS] = extend_rate_labels(
            self._stored_rate_labels(), replacement[CONF_RATE]
        )
        options.setdefault(CONF_ROOMS, self._rooms)
        return self.async_create_entry(title="", data=options)

    async def async_step_export_rate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Feed-in rate. One flat rate, or several if yours varies."""
        if user_input is None:
            existing = self._export_windows
            suggested = (
                {CONF_EXPORT_CENTS: existing[0][CONF_EXPORT_CENTS]}
                if len(existing) == 1
                else {}
            )
            schema = export_window_schema()
            return self.async_show_form(
                step_id="export_rate",
                data_schema=self.add_suggested_values_to_schema(schema, suggested)
                if suggested
                else schema,
                description_placeholders={"feed_in": self._export_summary()},
            )

        window = export_window_from_input(user_input)
        existing = list(self._export_windows)
        # A flat all-day rate replaces whatever was there; a partial-day rate
        # is added alongside, which is how a flat rate becomes time-varying.
        if window[CONF_START] == window[CONF_END]:
            existing = [window]
        else:
            existing = [
                w
                for w in existing
                if str(w[CONF_START]) != window[CONF_START]
                and str(w[CONF_START]) != str(w[CONF_END])
            ]
            existing.append(window)

        options = dict(self.config_entry.options)
        options[CONF_EXPORT_WINDOWS] = existing
        options.setdefault(CONF_ROOMS, self._rooms)
        options.setdefault(CONF_TARIFF_WINDOWS, self._windows)
        return self.async_create_entry(title="", data=options)

    async def async_step_supply_charge(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The daily supply charge. One figure for the whole house."""
        if user_input is None:
            current = self.config_entry.options.get(
                CONF_DAILY_SUPPLY_CENTS,
                self.config_entry.data.get(CONF_DAILY_SUPPLY_CENTS),
            )
            schema = vol.Schema(
                {vol.Optional(CONF_DAILY_SUPPLY_CENTS): _cents_selector()}
            )
            return self.async_show_form(
                step_id="supply_charge",
                data_schema=self.add_suggested_values_to_schema(
                    schema, {CONF_DAILY_SUPPLY_CENTS: current}
                )
                if current is not None
                else schema,
            )

        options = dict(self.config_entry.options)
        options[CONF_DAILY_SUPPLY_CENTS] = user_input.get(CONF_DAILY_SUPPLY_CENTS)
        options.setdefault(CONF_ROOMS, self._rooms)
        options.setdefault(CONF_TARIFF_WINDOWS, self._windows)
        return self.async_create_entry(title="", data=options)

    def _window_choice_schema(self) -> vol.Schema:
        """A picker over the tariff windows."""
        return vol.Schema(
            {
                vol.Required(CONF_START): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=str(window[CONF_START]),
                                label=describe_window(window),
                            )
                            for window in sort_windows(self._windows)
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    @property
    def _export_windows(self) -> list[dict[str, Any]]:
        return list(
            self.config_entry.options.get(
                CONF_EXPORT_WINDOWS,
                self.config_entry.data.get(CONF_EXPORT_WINDOWS, []),
            )
        )

    def _export_summary(self) -> str:
        if not self._export_windows:
            return "No feed-in rate configured."
        return "\n".join(
            describe_export_window(w) for w in self._export_windows
        )

    async def async_step_remove_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a tariff window."""
        if not self._windows:
            return self.async_abort(reason="no_windows")

        if user_input is None:
            return self.async_show_form(
                step_id="remove_window",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_START): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    selector.SelectOptionDict(
                                        value=str(window[CONF_START]),
                                        label=describe_window(window),
                                    )
                                    for window in sort_windows(self._windows)
                                ],
                                mode=selector.SelectSelectorMode.DROPDOWN,
                            )
                        )
                    }
                ),
                description_placeholders={"schedule": self._schedule_summary()},
            )

        remaining = [
            window
            for window in self._windows
            if str(window[CONF_START]) != user_input[CONF_START]
        ]
        options = dict(self.config_entry.options)
        options[CONF_TARIFF_WINDOWS] = remaining
        options.setdefault(CONF_ROOMS, self._rooms)
        return self.async_create_entry(title="", data=options)

    def _schedule_summary(self) -> str:
        """The schedule as it stands, plus any gap or overlap in it."""
        if not self._windows:
            return "No tariff windows configured."
        lines = [describe_window(w) for w in sort_windows(self._windows)]
        problems = schedule_gaps(self._windows)
        if problems:
            lines.append("")
            lines.append("Incomplete — " + "; ".join(problems) + ".")
            lines.append(
                "The schedule is ignored until it covers the whole day exactly once."
            )
        return "\n".join(lines)

    # ---- rooms --------------------------------------------------------

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
            (room for room in self._rooms if room[CONF_ROOM_ID] == self._editing), {}
        )

    def _stored_lockout_reasons(self) -> list[str]:
        return list(
            self.config_entry.options.get(
                CONF_LOCKOUT_REASONS,
                self.config_entry.data.get(CONF_LOCKOUT_REASONS, []),
            )
        )

    def _suggested_room(self) -> dict[str, Any]:
        existing = self._existing()
        if not existing:
            return {}
        return {
            **default_grace_suggestions(),
            **{k: v for k, v in existing.items() if v is not None},
            CONF_LOCKOUT_REASON: existing.get(CONF_LOCKOUT_REASON) or NOT_LOCKED_OUT,
        }

    def _suggested_lockout(self) -> dict[str, Any]:
        reason = self._existing().get(CONF_LOCKOUT_REASON)
        return {CONF_LOCKOUT_REASON: reason} if reason else {}

    def _suggested_bands(self) -> dict[str, float]:
        """A room being edited shows its own bands; a new one shows defaults."""
        existing = self._existing().get(CONF_BANDS, {})
        if existing:
            return bands_as_suggestions(existing)
        return default_band_suggestions()

    def _save_room(self) -> ConfigFlowResult:
        """Add or replace the room in the entry options."""
        options = dict(self.config_entry.options)
        # Replace the room being edited, and any room whose name produces the
        # same id, so editing a name does not leave the old room behind.
        replaced = {self._room[CONF_ROOM_ID], self._editing}
        rooms = [room for room in self._rooms if room[CONF_ROOM_ID] not in replaced]
        rooms.append(self._room)
        options[CONF_ROOMS] = rooms
        options[CONF_LOCKOUT_REASONS] = extend_lockout_reasons(
            self._stored_lockout_reasons(), self._room
        )
        options.setdefault(
            CONF_TARIFF_WINDOWS, self.config_entry.data.get(CONF_TARIFF_WINDOWS, [])
        )
        return self.async_create_entry(title="", data=options)
