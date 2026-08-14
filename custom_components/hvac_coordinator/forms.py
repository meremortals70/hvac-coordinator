"""Pure helpers for turning setup forms into stored configuration.

No Home Assistant imports, so the shaping of configuration can be tested
directly. `config_flow.py` owns the schemas and the step sequence; everything
here is data in, data out.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

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
    CONF_DIRECT_SUN_ENTITY,
    CONF_END,
    CONF_EXPORT_CENTS,
    CONF_FAN_ENTITY,
    CONF_HEAT_LOAD_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_ILLUMINANCE_ENTITY,
    CONF_IMPORT_CENTS,
    CONF_LOCKOUT_REASON,
    CONF_OCCUPIED_AFTER,
    CONF_OPENING_ENTITIES,
    CONF_OVERHANG_HEIGHT,
    CONF_OVERHANG_PROJECTION,
    CONF_PRESENCE_ENTITY,
    CONF_RATE,
    CONF_ROOM_ID,
    CONF_SLEEP_SCHEDULE_ENTITY,
    CONF_START,
    CONF_TEMPERATURE_ENTITY,
    CONF_VACANT_AFTER,
    CONF_WARNING_GRACE,
    CONF_WINDOW_DIRECTION,
    DEFAULT_BANDS,
    DEFAULT_LOCKOUT_REASONS,
    DEFAULT_RATE_LABELS,
    NOT_LOCKED_OUT,
)
from .grace import (
    DEFAULT_OCCUPIED_AFTER,
    DEFAULT_VACANT_AFTER,
    DEFAULT_WARNING_GRACE,
)
from .models import Mode

#: Modes that carry a band of their own. Unoccupied is off, precondition uses
#: the occupied band, coast inherits, lockout never actuates.
BAND_MODES = (Mode.OCCUPIED, Mode.SLEEP, Mode.PRECOOL)


def slug(name: str) -> str:
    """Derive a stable room id from the room name."""
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")


def room_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn the room form into a stored room.

    The lockout reason is deliberately left empty here. It is filled in by the
    lockout step, which is only reached when the box was ticked, so an
    unticked room can never carry a reason left over from a previous edit.
    """
    return {
        CONF_ROOM_ID: slug(user_input["name"]),
        "name": user_input["name"],
        CONF_CLIMATE_ENTITY: user_input[CONF_CLIMATE_ENTITY],
        CONF_TEMPERATURE_ENTITY: user_input.get(CONF_TEMPERATURE_ENTITY),
        CONF_HUMIDITY_ENTITY: user_input.get(CONF_HUMIDITY_ENTITY),
        CONF_PRESENCE_ENTITY: user_input.get(CONF_PRESENCE_ENTITY),
        CONF_SLEEP_SCHEDULE_ENTITY: user_input.get(CONF_SLEEP_SCHEDULE_ENTITY),
        CONF_ILLUMINANCE_ENTITY: user_input.get(CONF_ILLUMINANCE_ENTITY),
        CONF_DIRECT_SUN_ENTITY: user_input.get(CONF_DIRECT_SUN_ENTITY),
        CONF_WINDOW_DIRECTION: user_input.get(CONF_WINDOW_DIRECTION),
        CONF_OVERHANG_PROJECTION: user_input.get(CONF_OVERHANG_PROJECTION),
        CONF_OVERHANG_HEIGHT: user_input.get(CONF_OVERHANG_HEIGHT),
        CONF_OPENING_ENTITIES: user_input.get(CONF_OPENING_ENTITIES, []),
        CONF_COVER_ENTITIES: user_input.get(CONF_COVER_ENTITIES, []),
        CONF_OCCUPIED_AFTER: user_input.get(CONF_OCCUPIED_AFTER),
        CONF_VACANT_AFTER: user_input.get(CONF_VACANT_AFTER),
        CONF_WARNING_GRACE: user_input.get(CONF_WARNING_GRACE),
        CONF_ANNOUNCE: bool(user_input.get(CONF_ANNOUNCE, False)),
        CONF_ANNOUNCE_TARGETS: user_input.get(CONF_ANNOUNCE_TARGETS, []),
        CONF_LOCKOUT_REASON: _lockout_reason(user_input.get(CONF_LOCKOUT_REASON)),
    }


def _lockout_reason(chosen: str | None) -> str | None:
    """The stored lockout reason, or None when the room is not locked out.

    One dropdown answers both questions. The first option means not locked out,
    so there is no toggle to tick and no second screen to reach.
    """
    if chosen is None:
        return None
    reason = str(chosen).strip()
    if not reason or reason == NOT_LOCKED_OUT:
        return None
    return reason


def bands_from_input(user_input: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Collect only the bands where both bounds were supplied."""
    bands: dict[str, dict[str, float]] = {}
    for mode in BAND_MODES:
        low = user_input.get(f"{mode}_{CONF_BAND_LOW}")
        high = user_input.get(f"{mode}_{CONF_BAND_HIGH}")
        if low is not None and high is not None:
            bands[str(mode)] = {CONF_BAND_LOW: float(low), CONF_BAND_HIGH: float(high)}
    return bands


def bands_are_valid(bands: dict[str, dict[str, float]]) -> bool:
    """Every configured band must have its low below its high."""
    return all(
        values[CONF_BAND_LOW] < values[CONF_BAND_HIGH] for values in bands.values()
    )


def bands_as_suggestions(bands: dict[str, dict[str, float]]) -> dict[str, float]:
    """Flatten stored bands back into form field values, for editing."""
    return {
        f"{mode}_{bound}": values[bound]
        for mode, values in bands.items()
        for bound in (CONF_BAND_LOW, CONF_BAND_HIGH)
        if bound in values
    }


def default_band_suggestions() -> dict[str, float]:
    """The seeded bands, flattened into form field values.

    Every room starts from the same numbers, so a fresh install is consistent
    and needs no configuration to be sensible.
    """
    return bands_as_suggestions(DEFAULT_BANDS)


def default_grace_suggestions() -> dict[str, float | bool]:
    """Grace timings the room form arrives pre-filled with."""
    return {
        CONF_OCCUPIED_AFTER: DEFAULT_OCCUPIED_AFTER.total_seconds() / 60,
        CONF_VACANT_AFTER: DEFAULT_VACANT_AFTER.total_seconds() / 60,
        CONF_WARNING_GRACE: DEFAULT_WARNING_GRACE.total_seconds() / 60,
        CONF_ANNOUNCE: False,
    }


def known_lockout_reasons(stored: list[str]) -> list[str]:
    """The lockout dropdown: not-locked-out first, then every known reason."""
    return [NOT_LOCKED_OUT, *sorted({*DEFAULT_LOCKOUT_REASONS, *stored})]


def extend_lockout_reasons(stored: list[str], room: dict[str, Any]) -> list[str]:
    """Add this room's reason to the stored list if it is a new custom one.

    Built-in reasons are not stored: they are always offered, and storing them
    would leave stale copies behind if the built-in list ever changed.
    """
    reason = room.get(CONF_LOCKOUT_REASON)
    if (
        not reason
        or reason == NOT_LOCKED_OUT
        or reason in DEFAULT_LOCKOUT_REASONS
        or reason in stored
    ):
        return sorted(stored)
    return sorted([*stored, reason])


def known_rate_labels(stored: list[str]) -> list[str]:
    """Built-in rate labels plus any the user has added, deduplicated."""
    return sorted({*DEFAULT_RATE_LABELS, *stored})


def extend_rate_labels(stored: list[str], rate: str | None) -> list[str]:
    """Add a rate label to the stored list if it is a new custom one."""
    if not rate or rate in DEFAULT_RATE_LABELS or rate in stored:
        return sorted(stored)
    return sorted([*stored, rate])


def window_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn the tariff window form into a stored window."""
    return {
        CONF_START: _as_time_string(user_input[CONF_START]),
        CONF_END: _as_time_string(user_input[CONF_END]),
        CONF_RATE: str(user_input[CONF_RATE]).strip(),
        CONF_IMPORT_CENTS: user_input.get(CONF_IMPORT_CENTS),
        CONF_CONSTRAINTS: list(user_input.get(CONF_CONSTRAINTS, [])),
        CONF_COASTING_PERMITTED: bool(user_input.get(CONF_COASTING_PERMITTED, True)),
    }


def _as_time_string(value: Any) -> str:
    """Normalise a time selector value to HH:MM:SS.

    The time selector returns a string, but tests and stored data may hold a
    time object. Both are accepted so neither path is a special case.
    """
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) == 2:
        return f"{text}:00"
    return text


def sort_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Windows in start order, which is how a schedule reads."""
    return sorted(windows, key=lambda w: str(w.get(CONF_START, "")))


def describe_window(window: dict[str, Any]) -> str:
    """A one-line label for a window, for the remove picker."""
    start = str(window.get(CONF_START, "?"))[:5]
    end = str(window.get(CONF_END, "?"))[:5]
    rate = window.get(CONF_RATE, "?")
    constraints = window.get(CONF_CONSTRAINTS) or []
    price = window.get(CONF_IMPORT_CENTS)
    parts = [f"{start}–{end}", str(rate)]
    if price is not None:
        parts.append(f"{price}c/kWh")
    if constraints:
        parts.append(", ".join(sorted(constraints)))
    return "  ".join(parts[:2]) + ("  " + " — ".join(parts[2:]) if parts[2:] else "")


def schedule_gaps(windows: list[dict[str, Any]]) -> list[str]:
    """Describe any gap or overlap, so the user is told what is wrong.

    Returns an empty list for a schedule that covers the whole day exactly
    once. The controller ignores an invalid schedule entirely, so catching it
    here is the difference between a fixable message and silent nothing.
    """
    if not windows:
        return []
    ordered = sort_windows(windows)
    problems: list[str] = []

    first_start = str(ordered[0][CONF_START])[:5]
    if first_start != "00:00":
        problems.append(f"nothing covers 00:00 to {first_start}")

    for earlier, later in pairwise(ordered):
        end = str(earlier[CONF_END])[:5]
        start = str(later[CONF_START])[:5]
        if end != start:
            problems.append(
                f"gap between {end} and {start}" if end < start
                else f"overlap between {start} and {end}"
            )

    last_end = str(ordered[-1][CONF_END])[:5]
    if last_end not in ("00:00", "24:00"):
        problems.append(f"nothing covers {last_end} to midnight")

    return problems


def export_window_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn the feed-in form into a stored export window.

    A flat all-day rate is a window whose start equals its end, which is how
    the schedule expresses "the whole day". Adding a second window is what
    turns a flat rate into a time-varying one.
    """
    return {
        CONF_START: _as_time_string(user_input.get(CONF_START, "00:00:00")),
        CONF_END: _as_time_string(user_input.get(CONF_END, "00:00:00")),
        CONF_EXPORT_CENTS: float(user_input[CONF_EXPORT_CENTS]),
    }


def describe_export_window(window: dict[str, Any]) -> str:
    """A one-line label for a feed-in window."""
    start = str(window.get(CONF_START, "?"))[:5]
    end = str(window.get(CONF_END, "?"))[:5]
    cents = window.get(CONF_EXPORT_CENTS, 0)
    span = "all day" if start == end else f"{start}\u2013{end}"
    return f"{span}  {cents}c/kWh"


def window_as_suggestions(window: dict[str, Any]) -> dict[str, Any]:
    """Flatten a stored window back into form values, for editing."""
    return {
        CONF_START: window.get(CONF_START),
        CONF_END: window.get(CONF_END),
        CONF_RATE: window.get(CONF_RATE),
        CONF_IMPORT_CENTS: window.get(CONF_IMPORT_CENTS),
        CONF_CONSTRAINTS: list(window.get(CONF_CONSTRAINTS) or []),
        CONF_COASTING_PERMITTED: window.get(CONF_COASTING_PERMITTED, True),
    }


def describe_room(room: dict[str, Any]) -> str:
    """A room's whole configuration, as readable lines.

    Shown on the menu so the current settings can be read without opening the
    form that set them. A configuration you have to edit to inspect is a
    configuration nobody checks.
    """
    lines: list[str] = []

    def entry(label: str, value: Any, suffix: str = "") -> None:
        lines.append(f"  {label}: {value}{suffix}" if value else f"  {label}: —")

    lines.append(f"**{room.get('name', '?')}**")
    entry("Air conditioner", room.get(CONF_CLIMATE_ENTITY))
    entry("Temperature", room.get(CONF_TEMPERATURE_ENTITY))
    entry("Humidity", room.get(CONF_HUMIDITY_ENTITY))
    entry("Presence", room.get(CONF_PRESENCE_ENTITY))
    entry("Sleep schedule", room.get(CONF_SLEEP_SCHEDULE_ENTITY))
    entry("Heat source", room.get(CONF_HEAT_LOAD_ENTITY))
    entry("Air movement", room.get(CONF_FAN_ENTITY))
    entry("Windows face", room.get(CONF_WINDOW_DIRECTION))

    projection = room.get(CONF_OVERHANG_PROJECTION)
    if projection:
        height = room.get(CONF_OVERHANG_HEIGHT)
        lines.append(f"  Overhang: {projection} m out, {height} m above the glass")
    else:
        lines.append("  Overhang: none")

    openings = room.get(CONF_OPENING_ENTITIES) or []
    covers = room.get(CONF_COVER_ENTITIES) or []
    lines.append(f"  Windows and doors: {len(openings) or '—'}")
    lines.append(f"  Blinds: {len(covers) or '—'}")

    bands = room.get(CONF_BANDS) or {}
    if bands:
        described = ", ".join(
            f"{mode} {v[CONF_BAND_LOW]}–{v[CONF_BAND_HIGH]}"
            for mode, v in sorted(bands.items())
        )
        lines.append(f"  Bands: {described}")
    else:
        lines.append("  Bands: none — this room will never be actuated")

    lines.append(
        "  Waiting: {} min to start, {} min to stop".format(
            room.get(CONF_OCCUPIED_AFTER, "?"), room.get(CONF_VACANT_AFTER, "?")
        )
    )
    if room.get(CONF_ANNOUNCE):
        targets = room.get(CONF_ANNOUNCE_TARGETS) or []
        lines.append(f"  Announces before shutdown through {len(targets)} player(s)")

    reason = room.get(CONF_LOCKOUT_REASON)
    if reason:
        lines.append(f"  **LOCKED OUT — {reason}**")

    return "\n".join(lines)


def describe_configuration(
    rooms: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    export_windows: list[dict[str, Any]],
    daily_supply_cents: float | None,
    outdoor_entity_id: str | None,
) -> str:
    """Everything currently configured, for the menu screen."""
    lines: list[str] = []

    lines.append("**Rooms**")
    if rooms:
        for room in rooms:
            lines.append(describe_room(room))
    else:
        lines.append("  None configured.")

    lines.append("")
    lines.append("**Tariff** (applies to the whole house)")
    if windows:
        for window in sort_windows(windows):
            lines.append(f"  {describe_window(window)}")
        problems = schedule_gaps(windows)
        if problems:
            lines.append("  Incomplete — " + "; ".join(problems) + ".")
            lines.append("  The schedule is ignored until it covers the whole day.")
    else:
        lines.append("  No windows configured.")

    if export_windows:
        for window in export_windows:
            lines.append(f"  Feed-in: {describe_export_window(window)}")
    else:
        lines.append("  Feed-in: not configured")

    lines.append(
        f"  Daily supply charge: {daily_supply_cents}c"
        if daily_supply_cents is not None
        else "  Daily supply charge: not configured"
    )

    lines.append("")
    lines.append("**House**")
    lines.append(f"  Outdoor temperature: {outdoor_entity_id or '—'}")

    return "\n".join(lines)
