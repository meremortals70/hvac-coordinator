"""Tariff provider.

Pure. A window is a time span, a rate label, and a set of declared constraints.

Constraints are absolute rules, not price hints. They are declared in config,
never hard-coded here. No windows, rates or times appear in this file: those are
the user's tariff, entered at setup. Each is consumed by whichever system owns the relevant
actuator: grid_charge_battery by the Power automations, precool_opportunity and
no_grid_import by this controller. An unrecognised constraint is carried through
and reported, never silently dropped, so adding one needs no code change.

A constraint is never traded against price or comfort at runtime.
Architecture proposal v0.3, section 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from itertools import pairwise
from typing import Final

#: Constraints this controller acts on. Anything else is passed through to the
#: trace and reported as unrecognised, which is deliberate, not a gap.
CONSTRAINT_NO_GRID_IMPORT: Final = "no_grid_import"
CONSTRAINT_PRECOOL_OPPORTUNITY: Final = "precool_opportunity"
CONSTRAINT_GRID_CHARGE_BATTERY: Final = "grid_charge_battery"

KNOWN_CONSTRAINTS: Final = frozenset(
    {
        CONSTRAINT_NO_GRID_IMPORT,
        CONSTRAINT_PRECOOL_OPPORTUNITY,
        CONSTRAINT_GRID_CHARGE_BATTERY,
    }
)


@dataclass(frozen=True, slots=True)
class TariffWindow:
    """One window in the day. start is inclusive, end is exclusive."""

    start: time
    end: time
    rate: str
    constraints: frozenset[str] = field(default_factory=frozenset)
    #: False where coasting is the wrong call regardless of what the thermal
    #: model says, e.g. the cheap overnight window.
    coasting_permitted: bool = True

    def contains(self, at: time) -> bool:
        """Whether a time falls in this window.

        A window whose start equals its end covers the whole day. Without that
        case, a single 00:00-00:00 window would validate as a complete
        schedule and then match nothing.
        """
        if self.start == self.end:
            return True
        if self.start < self.end:
            return self.start <= at < self.end
        # Window wraps past midnight.
        return at >= self.start or at < self.end

    def unrecognised_constraints(self) -> frozenset[str]:
        return frozenset(self.constraints) - KNOWN_CONSTRAINTS


class TariffSchedule:
    """A day of windows. Validated on construction, so gaps surface at setup."""

    def __init__(self, windows: tuple[TariffWindow, ...]) -> None:
        self._windows = windows
        self._validate()

    def _validate(self) -> None:
        if not self._windows:
            raise ValueError("Tariff schedule has no windows")
        covered: list[TariffWindow] = sorted(self._windows, key=lambda w: w.start)
        if covered[0].start != time(0, 0):
            raise ValueError("Tariff schedule must start at 00:00")
        for earlier, later in pairwise(covered):
            if earlier.end != later.start:
                raise ValueError(
                    f"Gap or overlap in tariff schedule between {earlier.end} "
                    f"and {later.start}"
                )
        if covered[-1].end != time(0, 0):
            raise ValueError("Tariff schedule must run through to midnight")

    @property
    def windows(self) -> tuple[TariffWindow, ...]:
        return self._windows

    def window_at(self, at: time) -> TariffWindow:
        for window in self._windows:
            if window.contains(at):
                return window
        raise ValueError(f"No tariff window covers {at}")

    def unrecognised_constraints(self) -> frozenset[str]:
        """Every declared constraint this controller does not act on itself."""
        found: set[str] = set()
        for window in self._windows:
            found |= window.unrecognised_constraints()
        return frozenset(found)
