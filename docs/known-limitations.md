# Known limitations

Written plainly, because a limitation you discover yourself is worse than one
you were told about.

## Nothing has been run

**v0.3.0 has never been installed in Home Assistant, never loaded, and never
actuated anything.** The decision logic is unit tested — 43 tests over the pure
modules — but the Home Assistant surface has not been exercised. Treat the first
install as a test.

## It does not actuate

The controller decides which actuator step is correct and publishes that
decision. It does not yet carry it out.

Carrying it out means calling the regulation layer and the cover layer, and
those service schemas have not been read from their source. Nothing is called on
a guessed signature.

**What this means in practice:** installing this today gives you the comfort
index, the modes, the decision trace and the reasoning. It does not change what
your air conditioning does.

## Two modes cannot be entered

| Mode | Waiting on |
|---|---|
| `COAST` | The thermal model. `predicted_to_hold` is always unknown |
| `PRECOOL` | The demand forecast. `forecast_demand_ahead` is always false |

Both are implemented and tested. They are waiting on inputs.

## The deadline on heading home is recorded, not used

Working out when to start in order to arrive at comfort on time requires knowing
how fast the room responds, which is the thermal model. Until then,
preconditioning starts immediately.

## Two thresholds are placeholders

The dry-mode humidity threshold and the solar gain lux threshold are stand-ins
for decisions the thermal model should make.

The lux threshold is the weaker of the two, and it **will be wrong in rooms
whose illuminance sensor is not near the window** — what a sensor reads depends
entirely on where it sits. Detail in [Actuator ordering](actuator-ordering.md).

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
