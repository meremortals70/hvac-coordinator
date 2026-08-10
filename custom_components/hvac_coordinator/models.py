"""Data model for the coordinator.

Pure. No Home Assistant imports, so every one of these can be built and
inspected in a plain Python session or a unit test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from .hci import ComfortBand


class Mode(StrEnum):
    """Room modes. Mutually exclusive. Architecture proposal v0.3, section 4."""

    LOCKOUT = "lockout"
    UNOCCUPIED = "unoccupied"
    OCCUPIED = "occupied"
    SLEEP = "sleep"
    PRECONDITION = "precondition"
    PRECOOL = "precool"
    COAST = "coast"


#: Modes that carry a comfort band of their own.
#:
#: UNOCCUPIED is not among them. An unoccupied room is off, not held to a wider
#: envelope, and the only thing that brings it back on is a heading-home
#: request. COAST inherits the band of the occupancy mode it displaced;
#: PRECONDITION drives to an explicitly supplied target.
BAND_MODES: Final = (Mode.OCCUPIED, Mode.SLEEP, Mode.PRECOOL)


class ActuatorStep(StrEnum):
    """Cheapest first. Architecture proposal v0.3, section 6.

    Nothing may reach for a step until every step above it is exhausted.
    """

    NONE = "none"
    COVERS = "covers"
    FAN = "fan"
    DRY = "dry"
    COMPRESSOR = "compressor"


#: Ordering used by the selector. Index position is the cost rank.
ACTUATOR_ORDER: Final = (
    ActuatorStep.COVERS,
    ActuatorStep.FAN,
    ActuatorStep.DRY,
    ActuatorStep.COMPRESSOR,
)


@dataclass(frozen=True, slots=True)
class RoomConfig:
    """Static configuration for one room.

    Every room is seeded with identical defaults. There is no global setting
    and no inheritance. Rooms that genuinely differ get adjusted individually.
    Architecture proposal v0.3, section 5.
    """

    room_id: str
    name: str
    climate_entity_id: str
    bands: dict[Mode, ComfortBand]
    temperature_entity_id: str | None = None
    humidity_entity_id: str | None = None
    presence_entity_id: str | None = None
    #: A schedule, input_boolean or binary_sensor that is on while this room
    #: is in its sleeping hours. Without one, SLEEP is never entered.
    sleep_schedule_entity_id: str | None = None
    illuminance_entity_id: str | None = None
    #: A binary sensor that is on while the sun is on this room's windows.
    #: Adaptive Cover Pro publishes one per cover ("Sun Infront"). Without it,
    #: the controller falls back to whether the sun is above the horizon.
    direct_sun_entity_id: str | None = None
    opening_entity_ids: tuple[str, ...] = ()
    cover_entity_ids: tuple[str, ...] = ()
    #: Set for rooms that must never actuate. Carries the reason string that
    #: appears in the trace, e.g. "upstairs renovation".
    lockout_reason: str | None = None

    def band_for(self, mode: Mode) -> ComfortBand | None:
        return self.bands.get(mode)


@dataclass(frozen=True, slots=True)
class RoomInputs:
    """Everything the mode machine is allowed to look at, at one instant.

    Assembled from Home Assistant state by the coordinator and handed to the
    pure evaluator. Any value the coordinator could not read is None, and the
    evaluator must treat None as unknown rather than as a number.
    """

    now: datetime
    temperature_c: float | None = None
    relative_humidity: float | None = None
    presence: bool | None = None
    #: True while any opening in this room is open.
    opening_open: bool = False
    #: Sleep schedule is active for this room right now.
    sleep_schedule_active: bool = False
    #: When a heading-home request needs the room to be at comfort by. The
    #: target itself is never supplied: it is always the room's comfort band.
    precondition_deadline: datetime | None = None
    #: Declared on the active tariff window, not inferred from price.
    precool_opportunity: bool = False
    no_grid_import: bool = False
    #: Thermal model verdict. None until the model has converged for this room,
    #: in which case COAST is never entered and the fallback holds the band.
    predicted_to_hold: bool | None = None
    #: False in windows where coasting is the wrong call even if the room would
    #: hold, e.g. the cheap overnight window where energy is cheap and the
    #: battery should arrive at 06:00 full. Architecture proposal v0.3, s7.
    coasting_permitted: bool = True
    #: True when the forecast says the room will need cooling later today, the
    #: precondition for banking thermal mass in a free window.
    forecast_demand_ahead: bool = False
    #: Set by the heading-home request. The only thing that brings an
    #: unoccupied room back on.
    heading_home: bool = False
    #: Room illuminance in lux, where a sensor exists. Recorded, not acted on.
    #: It cannot tell you whether a cover is doing its job: a semi-transparent
    #: blind reads bright when fully closed.
    illuminance_lux: float | None = None
    #: Whether the sun is currently on this room's windows. Geometry, not light
    #: level — sun position against the window aspect. None when unknown.
    direct_sun: bool | None = None
    #: Whether this room has any covers under the controller's direction.
    has_covers: bool = False
    #: What the unit itself can do, read from the climate entity. The decision
    #: has to know: choosing dry on a unit with no dry mode would leave the
    #: actuator with a rejection and nothing to fall back to.
    can_cool: bool = True
    can_heat: bool = True
    can_dry: bool = True
    can_fan_only: bool = True
    #: Mean cover position across the room, 0 closed to 100 open, or None when
    #: no cover reports one. Covers are only worth commanding when they still
    #: have somewhere to go: without this the selector picks covers every cycle
    #: on an already-shut room and never escalates.
    cover_position: float | None = None


@dataclass(slots=True)
class DecisionTrace:
    """Why a room is doing what it is doing.

    Non-negotiable. Every evaluation produces one of these, whether or not
    anything changed. Architecture proposal v0.3, section 10.
    """

    room_id: str
    at: datetime
    mode: Mode
    #: The occupancy mode COAST displaced, so the band in force is visible.
    base_mode: Mode | None = None
    hci: float | None = None
    band_low: float | None = None
    band_high: float | None = None
    band_position: str | None = None
    target_dry_bulb_c: float | None = None
    actuator: ActuatorStep = ActuatorStep.NONE
    reasons: list[str] = field(default_factory=list)
    #: Steps that were considered and rejected, with why. This is what makes
    #: "cheapest first" auditable rather than asserted.
    rejected: list[str] = field(default_factory=list)
    #: Which way the room needs to move to reach its band: "cool", "heat" or
    #: None when it is already inside.
    demand: str | None = None
    #: The room's learned thermal coefficients and how converged they are.
    #: Published so a decision that depended on the model can be checked
    #: against what the model actually believed at the time.
    model: dict[str, Any] = field(default_factory=dict)

    def as_attributes(self) -> dict[str, Any]:
        """Flatten for publication as entity attributes."""
        return {
            "room_id": self.room_id,
            "evaluated_at": self.at.isoformat(),
            "mode": str(self.mode),
            "base_mode": str(self.base_mode) if self.base_mode else None,
            "hci": None if self.hci is None else round(self.hci, 2),
            "band_low": self.band_low,
            "band_high": self.band_high,
            "band_position": self.band_position,
            "target_dry_bulb_c": (
                None
                if self.target_dry_bulb_c is None
                else round(self.target_dry_bulb_c, 1)
            ),
            "demand": self.demand,
            "actuator": str(self.actuator),
            "reasons": list(self.reasons),
            "rejected": list(self.rejected),
            "model": dict(self.model),
        }
