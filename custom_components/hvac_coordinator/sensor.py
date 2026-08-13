"""Sensor platform.

Three per room: the mode with its full decision trace, the comfort index, and
the derived dry-bulb target. The trace is not a debug aid — without it the
system is unmaintainable, so it is published as entity attributes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import HvacConfigEntry
from .const import DOMAIN
from .coordinator import HvacCoordinator
from .entity import HvacRoomEntity
from .models import DecisionTrace, RoomConfig

# The coordinator centralises evaluation and this platform is read-only.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class HvacSensorDescription(SensorEntityDescription):
    """Describes an HVAC coordinator sensor."""

    value_fn: Callable[[DecisionTrace], str | float | None]
    attributes_fn: Callable[[DecisionTrace], dict[str, Any]] | None = None
    #: True where a missing value means the sensor is unavailable rather than
    #: unknown. The comfort index has no value when its source sensors are
    #: dead; the mode always has one.
    unavailable_when_none: bool = False


SENSORS: tuple[HvacSensorDescription, ...] = (
    HvacSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=["lockout", "unoccupied", "occupied", "sleep", "precondition", "precool", "coast"],
        value_fn=lambda trace: str(trace.mode),
        attributes_fn=lambda trace: trace.as_attributes(),
    ),
    HvacSensorDescription(
        key="comfort_index",
        translation_key="comfort_index",
        native_unit_of_measurement="HCI",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda trace: trace.hci,
        unavailable_when_none=True,
        attributes_fn=lambda trace: {
            "band_low": trace.band_low,
            "band_high": trace.band_high,
            "band_position": trace.band_position,
        },
    ),
    HvacSensorDescription(
        key="target_dry_bulb",
        translation_key="target_dry_bulb",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda trace: trace.target_dry_bulb_c,
        unavailable_when_none=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HvacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors, and add more as rooms are configured."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_rooms() -> None:
        new = [
            HvacRoomSensor(coordinator, room, description)
            for room_id, room in coordinator.rooms.items()
            if room_id not in known
            for description in SENSORS
        ]
        known.update(coordinator.rooms)
        if new:
            async_add_entities(new)

    _add_new_rooms()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_rooms))
    async_add_entities(
        [
            DemandForecastSensor(coordinator, entry),
            *[
                HubSensor(coordinator, entry, description)
                for description in HUB_SENSORS
            ],
        ]
    )


class HvacRoomSensor(HvacRoomEntity, SensorEntity):
    """A sensor reporting one aspect of a room's decision."""

    entity_description: HvacSensorDescription

    def __init__(
        self,
        coordinator: HvacCoordinator,
        room: RoomConfig,
        description: HvacSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, room)
        self.entity_description = description
        self._attr_unique_id = f"{room.room_id}_{description.key}"

    @property
    def available(self) -> bool:
        """Unavailable when the reading behind this sensor is missing."""
        if not super().available:
            return False
        if not self.entity_description.unavailable_when_none:
            return True
        trace = self.trace
        return trace is not None and self.entity_description.value_fn(trace) is not None

    @property
    def native_value(self) -> str | float | None:
        """Return the current value."""
        if (trace := self.trace) is None:
            return None
        return self.entity_description.value_fn(trace)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the supporting detail, including the decision trace."""
        if (trace := self.trace) is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(trace)


@dataclass(frozen=True, kw_only=True)
class HubSensorDescription(SensorEntityDescription):
    """Describes a house-wide sensor on the coordinator device."""

    value_fn: Callable[[HvacCoordinator], str | float | None]
    attributes_fn: Callable[[HvacCoordinator], dict[str, Any]] | None = None


def _tariff_rate(coordinator: HvacCoordinator) -> str | None:
    """The rate label in force right now."""
    if coordinator.tariff is None:
        return None
    try:
        return coordinator.tariff.window_at(dt_util.now().time()).rate
    except ValueError:
        return None


def _import_price(coordinator: HvacCoordinator) -> float | None:
    if coordinator.tariff is None:
        return None
    return coordinator.tariff.import_cents_at(dt_util.now().time())


def _export_price(coordinator: HvacCoordinator) -> float | None:
    if coordinator.tariff is None:
        return None
    return coordinator.tariff.export_cents_at(dt_util.now().time())


def _supply_charge(coordinator: HvacCoordinator) -> float | None:
    return None if coordinator.tariff is None else coordinator.tariff.daily_supply_cents


def _constraints(coordinator: HvacCoordinator) -> str | None:
    """Which constraints are in force, as a readable string."""
    if coordinator.tariff is None:
        return None
    try:
        window = coordinator.tariff.window_at(dt_util.now().time())
    except ValueError:
        return None
    return ", ".join(sorted(window.constraints)) if window.constraints else "none"


def _projected_cost(coordinator: HvacCoordinator) -> float | None:
    """What the forecast energy would cost at the configured prices.

    Per-window, because the whole point of a time-of-use plan is that the same
    kWh costs different amounts depending on when it is used.
    """
    forecast = coordinator.forecast
    if forecast is None or coordinator.tariff is None:
        return None
    total = 0.0
    priced = False
    for window in forecast.windows:
        price = coordinator.tariff.import_cents_at(window.start)
        if price is None:
            continue
        priced = True
        total += window.kwh * price
    return round(total / 100.0, 2) if priced else None


HUB_SENSORS: tuple[HubSensorDescription, ...] = (
    HubSensorDescription(
        key="tariff_rate",
        translation_key="tariff_rate",
        value_fn=_tariff_rate,
        attributes_fn=lambda c: {
            "windows": [
                {
                    "start": w.start.isoformat(),
                    "end": w.end.isoformat(),
                    "rate": w.rate,
                    "import_cents_per_kwh": w.import_cents,
                    "constraints": sorted(w.constraints),
                    "coasting_permitted": w.coasting_permitted,
                }
                for w in c.tariff.windows
            ]
            if c.tariff
            else [],
        },
    ),
    HubSensorDescription(
        key="import_price",
        translation_key="import_price",
        native_unit_of_measurement="c/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_import_price,
    ),
    HubSensorDescription(
        key="export_price",
        translation_key="export_price",
        native_unit_of_measurement="c/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_export_price,
        attributes_fn=lambda c: {
            "windows": [
                {
                    "start": w.start.isoformat(),
                    "end": w.end.isoformat(),
                    "export_cents_per_kwh": w.export_cents,
                }
                for w in c.tariff.export_windows
            ]
            if c.tariff
            else [],
        },
    ),
    HubSensorDescription(
        key="daily_supply_charge",
        translation_key="daily_supply_charge",
        native_unit_of_measurement="c",
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_supply_charge,
    ),
    HubSensorDescription(
        key="active_constraints",
        translation_key="active_constraints",
        value_fn=_constraints,
    ),
    HubSensorDescription(
        key="projected_cost",
        translation_key="projected_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        suggested_display_precision=2,
        value_fn=_projected_cost,
    ),
    HubSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.outdoor_reading(),
    ),
    HubSensorDescription(
        key="rooms_configured",
        translation_key="rooms_configured",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: len(c.rooms),
        attributes_fn=lambda c: {
            "rooms": sorted(room.name for room in c.rooms.values())
        },
    ),
)


def hub_device_info(entry: HvacConfigEntry) -> DeviceInfo:
    """The house-wide device every global setting appears under."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="HVAC Coordinator",
        manufacturer="HVAC Coordinator",
        model="Coordinator",
        entry_type=DeviceEntryType.SERVICE,
    )


class HubSensor(CoordinatorEntity[HvacCoordinator], SensorEntity):
    """A house-wide value, visible rather than buried in a config form."""

    _attr_has_entity_name = True
    entity_description: HubSensorDescription

    def __init__(
        self,
        coordinator: HvacCoordinator,
        entry: HvacConfigEntry,
        description: HubSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(entry)

    @property
    def native_value(self) -> str | float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the supporting detail."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator)


class DemandForecastSensor(CoordinatorEntity[HvacCoordinator], SensorEntity):
    """Projected HVAC energy over the horizon.

    This is the published contract with whatever owns the battery. It carries
    **no vendor concepts**: projected kWh, a per-window breakdown, and the
    constraints in force. A Powerwall automation and a Sungrow automation each
    translate it into their own primitives.

    The controller never writes battery actuators itself. Two writers on one
    actuator fail silently, and battery control is vendor-specific — coding one
    in would tie this project to a single manufacturer.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "demand_forecast"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator: HvacCoordinator, entry: HvacConfigEntry) -> None:
        """Initialize the forecast sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_demand_forecast"
        self._attr_device_info = hub_device_info(entry)

    @property
    def available(self) -> bool:
        """Available once a forecast has been produced."""
        return super().available and self.coordinator.forecast is not None

    @property
    def native_value(self) -> float | None:
        """Projected energy over the horizon, in kWh."""
        forecast = self.coordinator.forecast
        return None if forecast is None else forecast.total_kwh

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The per-window breakdown, so automations can read either."""
        forecast = self.coordinator.forecast
        return None if forecast is None else forecast.as_attributes()
