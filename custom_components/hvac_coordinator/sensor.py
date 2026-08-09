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
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HvacConfigEntry
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
