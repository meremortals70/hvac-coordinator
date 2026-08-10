# Known limitations

Written plainly, because a limitation you discover yourself is worse than one
you were told about.

## Nothing has been run

**v0.4.0 has never been installed in Home Assistant, never loaded, and never
actuated anything.** The decision logic is unit tested — 128 tests over the pure
modules — but the Home Assistant surface has not been exercised. Treat the first
install as a test.

## Actuation is wired but unproven

The controller now carries out its decisions: it sets the climate entity's HVAC
mode, temperature and fan mode, and moves covers.

**It has never done this against a real unit.** Every service call was written
against the service definitions read from source — Home Assistant's own climate
and cover components, Versatile Thermostat's `services.yaml`, and Adaptive
Cover Pro's — but reading a schema is not the same as watching a compressor
start.

Two things reduce the blast radius, and neither removes it:

- Every call is capability-checked first. An HVAC mode the unit does not
  advertise is a rejection in the trace, not a failed service call.
- Unchanged decisions are not re-sent, so a stable room is not commanded every
  30 seconds.

Watch the first day. A room in lockout should command nothing at all, which is
the cheapest way to confirm the gate works before trusting the rest.

## Two modes cannot be entered

| Mode | Waiting on |
|---|---|
| `COAST` | The thermal model. `predicted_to_hold` is always unknown |
| `PRECOOL` | The demand forecast. `forecast_demand_ahead` is always false |

## There is no demand forecast

The vendor-neutral projected-energy sensor described in the architecture is not
built, so nothing downstream can read what this controller expects to draw.

Both are implemented and tested. They are waiting on inputs.

## The deadline on heading home is recorded, not acted on

The model can now answer how long a room takes to reach comfort, but
preconditioning still starts immediately rather than working backwards from the
deadline. The arithmetic exists; the scheduling around it does not.

## Two thresholds are placeholders

The dry-mode humidity threshold and the solar gain lux threshold are stand-ins
for decisions the thermal model should make.

The lux threshold is the weaker of the two, and it **will be wrong in rooms
whose illuminance sensor is not near the window** — what a sensor reads depends
entirely on where it sits. Detail in [Actuator ordering](actuator-ordering.md).

## The tariff is entered one window at a time

There is no bulk import and no way to copy a schedule between installations.
Six windows is six trips through the form.

## Sun detection is one compass direction per room

The controller works out sun-on-glass from the sun's position and the direction
the room's windows face. That is right for a room with windows on one wall and
wrong for a corner room, a room with a verandah, or one shaded by a tree at
certain hours.

Where that matters, point the room's sun-on-window setting at your own binary
sensor instead — it overrides the calculation entirely.

A room with no direction and no sensor never uses its blinds, because the
controller will not move them on a guess.

## The comfort index is one opinion

Steadman apparent temperature with wind zero is defensible and behaves correctly
with humidity, but comfort depends on clothing, metabolic rate, air movement and
radiant temperature, none of which are measured. The band table is derived from
ASHRAE 55's assumptions about a seated person, and those assumptions may not be
yours.

Confidence that the band table suits any particular household: **70%**. Adjust
against how the room actually feels rather than trusting the numbers.

## It never writes to your battery

Deliberate, and not changing. See [Architecture](architecture.md).

## Multi-head units

No arbitration between heads sharing a compressor. Each room is evaluated
independently. In a climate where rooms on a shared compressor will not want
opposing modes this is harmless; elsewhere it is a real gap.

## Single instance

One config entry for the whole house. Rooms live inside it.

## Not in Home Assistant core

A custom integration cannot be awarded a quality scale tier — Custom is a
special tier alongside Internal and Legacy. The project tracks compliance
against all 54 rules in `quality_scale.yaml` so that submission remains
possible, but it is not graded today.
