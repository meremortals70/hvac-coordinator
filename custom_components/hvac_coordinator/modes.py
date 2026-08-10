"""Mode evaluation and actuator selection.

Pure. One function decides the mode, one decides the actuator step, and both
write their reasoning into the trace as they go. Nothing here talks to Home
Assistant, so the whole decision path is testable without a running instance.

PRECEDENCE — architecture proposal v0.3, section 4
--------------------------------------------------
    LOCKOUT        beats everything, always
    PRECONDITION   beats presence, which is the point of it. Entered by a
                   heading-home request, and driven to the occupied band
    PRECOOL        free/cheap window plus forecast heat ahead
    COAST          model says the band holds unaided, and the window permits it
    SLEEP          sleep schedule and presence
    OCCUPIED       presence
    UNOCCUPIED     no presence

COAST is returned as the mode but carries base_mode, so the band that applies
is the one the displaced occupancy mode would have used. Without that, COAST
would have no band and the fallback out of COAST would have nothing to compare
against.
"""

from __future__ import annotations

from .hci import (
    SOLVE_MAX_C,
    SOLVE_MIN_C,
    ComfortBand,
    comfort_index,
    dry_bulb_for_index,
)
from .models import (
    BAND_MODES,
    ActuatorStep,
    DecisionTrace,
    Mode,
    RoomConfig,
    RoomInputs,
)


def _occupancy_mode(inputs: RoomInputs, trace: DecisionTrace) -> Mode:
    """Resolve the presence-driven mode. Never returns LOCKOUT."""
    if inputs.presence is None:
        # Unknown presence is not absence. A failed sensor must not turn the
        # room off, so hold an occupied mode and say so. The sleep schedule
        # still applies: a dead presence sensor at 2am should not put the room
        # on the day band.
        if inputs.sleep_schedule_active:
            trace.reasons.append("presence unknown, holding sleep")
            return Mode.SLEEP
        trace.reasons.append("presence unknown, holding occupied")
        return Mode.OCCUPIED

    if not inputs.presence:
        trace.reasons.append("no presence")
        return Mode.UNOCCUPIED

    if inputs.sleep_schedule_active:
        trace.reasons.append("presence and sleep schedule active")
        return Mode.SLEEP

    trace.reasons.append("presence")
    return Mode.OCCUPIED


def evaluate_mode(
    config: RoomConfig, inputs: RoomInputs, trace: DecisionTrace
) -> tuple[Mode, Mode | None]:
    """Return (mode, base_mode). base_mode is set only when mode is COAST."""
    if config.lockout_reason is not None:
        trace.reasons.append(f"lockout: {config.lockout_reason}")
        return Mode.LOCKOUT, None

    if inputs.heading_home:
        trace.reasons.append("heading home, overrides presence")
        return Mode.PRECONDITION, None

    base = _occupancy_mode(inputs, trace)

    # Precool banks thermal mass in a room that is going to be lived in. An
    # unoccupied room is off, and precooling one would spend energy cooling a
    # room nobody is in.
    if inputs.precool_opportunity:
        if base is Mode.UNOCCUPIED:
            trace.rejected.append("precool: room is unoccupied")
        elif not inputs.forecast_demand_ahead:
            trace.rejected.append("precool: window open but no demand forecast ahead")
        else:
            trace.reasons.append(
                "precool window declared and demand forecast ahead"
            )
            return Mode.PRECOOL, None

    if inputs.predicted_to_hold is True and inputs.coasting_permitted:
        trace.reasons.append("thermal model predicts the band holds unaided")
        return Mode.COAST, base

    if inputs.predicted_to_hold is True and not inputs.coasting_permitted:
        trace.rejected.append(
            "coast: model says it would hold, but this window does not permit "
            "coasting"
        )
    elif inputs.predicted_to_hold is None:
        trace.rejected.append("coast: thermal model has not converged for this room")

    return base, None


def band_in_force(
    config: RoomConfig, mode: Mode, base_mode: Mode | None
) -> ComfortBand | None:
    """The band that applies to this mode.

    There is only ever one comfort definition per room, and it is the band.
    COAST follows back to the occupancy mode it displaced. PRECONDITION uses
    the occupied band: bringing a room back on means bringing it to comfort,
    and there is nothing else to drive toward.
    """
    if mode is Mode.COAST and base_mode is not None:
        return config.band_for(base_mode)
    if mode is Mode.PRECONDITION:
        return config.band_for(Mode.OCCUPIED)
    if mode in BAND_MODES:
        return config.band_for(mode)
    return None


#: How far above the band air movement alone is worth trying, in HCI. Beyond
#: this a fan is just noise. Fixed internal, not a setting.
FAN_MARGIN_HCI = 0.5

#: PLACEHOLDER. Indoor relative humidity above which the load is treated as
#: latent enough that dry mode beats cooling for the same draw.
#:
#: This is a stand-in. Architecture section 8 has the thermal model learning
#: the sensible and latent terms separately, and once it does, that split makes
#: this decision properly. A single humidity threshold cannot: 65% at 22 C and
#: 65% at 30 C are different loads. Replace when the model converges.
DRY_MODE_RH_THRESHOLD = 65.0

#: How near an extreme a cover counts as already there, in percent. A blind at
#: 3% is shut for our purposes; commanding it to 0 achieves nothing, and
#: without this check the ordering picks covers every cycle on an already-shut
#: room and never escalates to the next step.
COVER_TRAVEL_MARGIN = 5.0


def _demand_direction(mode: Mode, band: ComfortBand, hci: float) -> str | None:
    """Which way the room needs to move, or None if it is where it should be.

    PRECOOL is the exception: it is driving to the low bound, not to the
    middle, so it keeps cooling while the room is above that bound even though
    the room is technically inside the band.
    """
    if mode is Mode.PRECOOL:
        return "cool" if hci > band.low else None
    if hci > band.high:
        return "cool"
    if hci < band.low:
        return "heat"
    return None


def _covers_can_help(demand: str, position: float | None) -> bool:
    """Whether the covers still have travel left in the useful direction."""
    if position is None:
        # No position reported. Command them once and let the next evaluation
        # see the result, rather than assuming either way.
        return True
    if demand == "cool":
        return position > COVER_TRAVEL_MARGIN
    return position < (100.0 - COVER_TRAVEL_MARGIN)


def select_actuator(
    mode: Mode,
    band: ComfortBand | None,
    hci: float | None,
    inputs: RoomInputs,
    trace: DecisionTrace,
) -> ActuatorStep:
    """Cheapest first: covers, then fan, then dry, then compressor.

    Nothing reaches the compressor until everything above it has been ruled
    out, and every rule-out is written into the trace. Architecture proposal
    v0.3, section 6.
    """
    if mode is Mode.LOCKOUT:
        trace.rejected.append("all actuators: room is in lockout")
        return ActuatorStep.NONE

    if mode is Mode.UNOCCUPIED:
        # Not a wider envelope. Off. Only a heading-home request brings it back.
        trace.rejected.append("all actuators: room unoccupied, air conditioning off")
        return ActuatorStep.NONE

    if inputs.opening_open:
        trace.rejected.append("all actuators: an opening in this room is open")
        return ActuatorStep.NONE

    if mode is Mode.COAST:
        trace.rejected.append("compressor: coasting, model predicts the band holds")
        return ActuatorStep.NONE

    if hci is None or band is None:
        # No reading, or no band configured for this mode. A room with no bands
        # never actuates: there is no default to fall back on, and inventing
        # one would be worse.
        trace.rejected.append("all actuators: no comfort reading or no band in force")
        return ActuatorStep.NONE

    demand = _demand_direction(mode, band, hci)
    trace.demand = demand
    if demand is None:
        trace.reasons.append("within band")
        return ActuatorStep.NONE

    # --- 1. Covers. Free, and they work in both directions: block gain when
    # the room is too warm, admit it when the room is too cold.
    #
    # The gate is whether the sun is on this room's windows, which is geometry.
    # Indoor light level cannot answer it: a semi-transparent blind reads
    # bright when it is fully closed, so lux would say there is nothing to
    # block at exactly the moment the blind is already blocking.
    if not inputs.has_covers:
        trace.rejected.append("covers: none configured for this room")
    elif inputs.direct_sun is None:
        trace.rejected.append("covers: cannot tell whether the sun is on this room")
    elif not inputs.direct_sun:
        trace.rejected.append("covers: no sun on this room to act on")
    elif not _covers_can_help(demand, inputs.cover_position):
        # Already where they need to be. Saying so is what lets the ordering
        # move on to the next step instead of choosing covers forever.
        trace.rejected.append(
            "covers: already closed against the gain"
            if demand == "cool"
            else "covers: already open to the gain"
        )
    else:
        trace.reasons.append(
            "covers: blocking solar gain"
            if demand == "cool"
            else "covers: admitting solar gain"
        )
        return ActuatorStep.COVERS

    if demand == "heat":
        # Fan and dry mode do not heat. Once covers are out, the compressor is
        # the only remaining step.
        trace.rejected.append("fan and dry: neither adds heat")
        if not inputs.can_heat:
            trace.rejected.append("compressor: this unit cannot heat")
            return ActuatorStep.NONE
        trace.reasons.append("compressor: heating")
        return ActuatorStep.COMPRESSOR

    # --- 2. Fan. Air movement, no compressor. Worth trying only when the room
    # is marginally out of band.
    overshoot = hci - (band.low if mode is Mode.PRECOOL else band.high)
    if not inputs.can_fan_only:
        trace.rejected.append("fan: this unit has no fan-only mode")
    elif overshoot <= FAN_MARGIN_HCI:
        trace.reasons.append("fan: marginally above band, air movement should carry it")
        return ActuatorStep.FAN
    else:
        trace.rejected.append(
            f"fan: {overshoot:.1f} HCI above band, beyond what air movement carries"
        )

    # --- 3. Dry mode. A latent-dominated load costs far less to shift with dry
    # mode on a low fan than with cooling.
    latent = (
        inputs.relative_humidity is not None
        and inputs.relative_humidity >= DRY_MODE_RH_THRESHOLD
    )
    if not inputs.can_dry:
        trace.rejected.append("dry: this unit has no dry mode")
    elif latent:
        trace.reasons.append("dry: load is latent, dehumidify rather than cool")
        return ActuatorStep.DRY
    else:
        trace.rejected.append("dry: load is sensible, not latent")

    # --- 4. Compressor. Everything cheaper has been ruled out above.
    if not inputs.can_cool:
        trace.rejected.append("compressor: this unit cannot cool")
        return ActuatorStep.NONE
    trace.reasons.append("compressor: cooling")
    return ActuatorStep.COMPRESSOR


def evaluate_room(
    config: RoomConfig, inputs: RoomInputs
) -> DecisionTrace:
    """Full evaluation for one room. Always returns a trace."""
    trace = DecisionTrace(room_id=config.room_id, at=inputs.now, mode=Mode.LOCKOUT)

    mode, base = evaluate_mode(config, inputs, trace)
    trace.mode = mode
    trace.base_mode = base

    hci: float | None = None
    if inputs.temperature_c is not None and inputs.relative_humidity is not None:
        hci = comfort_index(inputs.temperature_c, inputs.relative_humidity)
        trace.hci = hci
    else:
        trace.rejected.append("comfort index: temperature or humidity unavailable")

    band = band_in_force(config, mode, base)
    if band is not None:
        trace.band_low = band.low
        trace.band_high = band.high
        if hci is not None:
            trace.band_position = band.position(hci)

    # The dry bulb target the AC is actually asked for. The user never sets
    # this: it is derived from the band and the measured humidity, so a humid
    # night produces a different setpoint for the same felt comfort.
    if band is not None and inputs.relative_humidity is not None:
        target_hci = band.low if mode is Mode.PRECOOL else band.midpoint
        target_c = dry_bulb_for_index(target_hci, inputs.relative_humidity)
        if target_c in (SOLVE_MIN_C, SOLVE_MAX_C):
            # The band and the measured humidity together imply a setpoint
            # outside anything worth asking of the hardware. Clamped, and said
            # so, rather than passed through.
            trace.rejected.append(
                f"target: HCI {target_hci:.1f} at {inputs.relative_humidity:.0f}% "
                f"implies a setpoint outside {SOLVE_MIN_C:.0f}-{SOLVE_MAX_C:.0f} C, "
                f"clamped to {target_c:.0f} C"
            )
        trace.target_dry_bulb_c = target_c

    trace.actuator = select_actuator(mode, band, hci, inputs, trace)
    return trace
