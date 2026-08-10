"""Sun geometry.

Pure. No Home Assistant imports.

Answers one question: **is the sun on this room's windows right now?**

WHY THIS EXISTS
---------------
Covers are the cheapest actuator, but only when there is something for them to
block. The obvious signal — indoor light level — does not work. A
semi-transparent blind reads bright when it is fully closed, so illuminance
reports nothing to block at exactly the moment the blind is already blocking.

The signal that does work is geometry: where the sun is, against which way the
window faces. That needs no sensor in the room and no assumption about the
blind's material.

WHAT IT NEEDS
-------------
The direction the window faces, chosen once at setup, and the sun's position,
which Home Assistant already publishes as `sun.sun` with `azimuth` and
`elevation` attributes.

Nothing else. No external integration, no extra entity to create.
"""

from __future__ import annotations

#: Half-width of the acceptance angle either side of the window normal, in
#: degrees. Beyond about 90 degrees off-normal the sun is behind the wall and
#: cannot reach the glass at all, so 90 is geometry rather than a preference.
ACCEPTANCE_HALF_ANGLE = 90.0

#: Below this elevation the sun is on the horizon: weak, and usually behind
#: something. Above zero rather than at it, because a sun a degree up is
#: contributing nothing worth moving a blind for.
MIN_ELEVATION = 5.0

#: Compass directions offered at setup, with the azimuth each faces.
#: Azimuth is degrees clockwise from north, matching what `sun.sun` reports.
WINDOW_DIRECTIONS: dict[str, float] = {
    "north": 0.0,
    "north_east": 45.0,
    "east": 90.0,
    "south_east": 135.0,
    "south": 180.0,
    "south_west": 225.0,
    "west": 270.0,
    "north_west": 315.0,
}


def angle_between(first: float, second: float) -> float:
    """Smallest angle between two compass bearings, 0 to 180 degrees."""
    difference = abs(first - second) % 360.0
    return 360.0 - difference if difference > 180.0 else difference


def sun_on_window(
    sun_azimuth: float | None,
    sun_elevation: float | None,
    window_azimuth: float | None,
) -> bool | None:
    """Whether the sun is on a window facing `window_azimuth`.

    None means the question cannot be answered — no sun position, or no window
    direction configured — and the caller must treat that as "do not move the
    covers" rather than as either answer.
    """
    if sun_azimuth is None or sun_elevation is None or window_azimuth is None:
        return None
    if sun_elevation < MIN_ELEVATION:
        return False
    return angle_between(sun_azimuth, window_azimuth) <= ACCEPTANCE_HALF_ANGLE


def azimuth_for_direction(direction: str | None) -> float | None:
    """Azimuth for a configured compass direction, or None if unset."""
    if direction is None:
        return None
    return WINDOW_DIRECTIONS.get(direction)
