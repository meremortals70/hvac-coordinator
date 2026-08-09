"""Home Comfort Index (HCI).

Pure functions. This module imports nothing from Home Assistant so it can be
unit tested on its own.

WHAT THE INDEX IS
-----------------
HCI is the Steadman shaded apparent temperature with wind set to zero:

    AT = Ta + 0.33 * e - 0.70 * ws - 4.00          (Steadman / BoM, shade)
    e  = (RH / 100) * 6.105 * exp(17.27 * Ta / (237.7 + Ta))

Indoors ws = 0, so:

    HCI = Ta + 0.33 * e - 4.00

Ta is indoor dry bulb in degrees C, RH is indoor relative humidity in percent,
e is water vapour pressure in hPa.

WHY THIS FORMULA AND NOT HEAT INDEX OR HUMIDEX
----------------------------------------------
Both of those are undefined below roughly 26-27 C. The sleep band is 18-20, so
they cannot express it. Steadman AT is continuous across the whole range this
house operates in and collapses toward dry bulb as the air dries out.

The -4.00 constant is kept so the scale matches the convention the BoM feed
already uses. It is a constant offset: it shifts every HCI number by the same
amount and changes no decision. The band defaults are set against this scale.

DO NOT COMPARE HCI TO THE BoM OUTDOOR APPARENT TEMPERATURE.
The outdoor figure carries a wind term and is a different quantity.
Architecture proposal v0.3, section 5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Steadman constants.
_VAPOUR_A = 6.105
_VAPOUR_B = 17.27
_VAPOUR_C = 237.7
_E_COEFF = 0.33
_OFFSET = -4.00

# Bounds on the derived dry bulb setpoint, in degrees C. These are a hard limit
# on what will ever be asked of an air conditioner, not just solve bounds: a
# band and a humidity that together imply 45 C are a configuration error, and
# the controller clamps and records it rather than passing it to the hardware.
# Callers detect a clamp by comparing the result to these values.
SOLVE_MIN_C = 5.0
SOLVE_MAX_C = 40.0
_SOLVE_TOLERANCE = 0.001
_SOLVE_MAX_ITERATIONS = 60


def vapour_pressure_hpa(temp_c: float, relative_humidity: float) -> float:
    """Water vapour pressure in hPa from dry bulb and relative humidity."""
    saturation = _VAPOUR_A * math.exp(
        (_VAPOUR_B * temp_c) / (_VAPOUR_C + temp_c)
    )
    return (relative_humidity / 100.0) * saturation


def comfort_index(temp_c: float, relative_humidity: float) -> float:
    """Return the HCI for an indoor temperature and relative humidity."""
    return temp_c + _E_COEFF * vapour_pressure_hpa(temp_c, relative_humidity) + _OFFSET


def dry_bulb_for_index(target_hci: float, relative_humidity: float) -> float:
    """Return the dry bulb setpoint that produces target_hci at this humidity.

    This is the inverse of comfort_index(). It has no closed form because the
    vapour pressure term itself depends on temperature, so it is solved by
    bisection. comfort_index() rises monotonically with temperature at fixed
    humidity, which is what makes bisection safe here.

    This function is the whole reason the user never sets a setpoint. The user
    states how the room should feel; this works out what to ask the AC for.
    """
    low = SOLVE_MIN_C
    high = SOLVE_MAX_C

    # Outside the solvable range, clamp. The caller is expected to notice, by
    # comparing against SOLVE_MIN_C / SOLVE_MAX_C, and record it.
    if comfort_index(low, relative_humidity) >= target_hci:
        return low
    if comfort_index(high, relative_humidity) <= target_hci:
        return high

    for _ in range(_SOLVE_MAX_ITERATIONS):
        middle = (low + high) / 2.0
        value = comfort_index(middle, relative_humidity)
        if abs(value - target_hci) < _SOLVE_TOLERANCE:
            return middle
        if value < target_hci:
            low = middle
        else:
            high = middle

    return (low + high) / 2.0


@dataclass(frozen=True, slots=True)
class ComfortBand:
    """The only comfort configuration that exists. One low, one high.

    Per room, per mode. No global, no inheritance.
    Architecture proposal v0.3, section 5.
    """

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low >= self.high:
            raise ValueError(
                f"Comfort band low ({self.low}) must be below high ({self.high})"
            )

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2.0

    def contains(self, hci: float) -> bool:
        return self.low <= hci <= self.high

    def position(self, hci: float) -> str:
        """Where a reading sits relative to the band: below, within or above."""
        if hci < self.low:
            return "below"
        if hci > self.high:
            return "above"
        return "within"
