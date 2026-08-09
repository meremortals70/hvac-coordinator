# Actuator ordering

Before increasing compressor load, exhaust the cheaper options in order. This is
the largest energy saving available and the reason covers belong inside this
controller rather than running as a separate automation.

```
1. Covers      free
2. Fan         circulation only
3. Dry         latent load at a fraction of cooling draw
4. Compressor  cooling or heating
```

Nothing reaches a step until every step above it has been ruled out, and **every
rule-out is written into the decision trace** with the reason. That is what
makes the ordering auditable rather than merely asserted.

## Gates, checked before any actuator

| Condition | Result |
|---|---|
| Room in lockout | Nothing |
| Room unoccupied | Nothing |
| An opening in the room is open | Nothing |
| Coasting | Nothing |
| No temperature or humidity reading | Nothing |
| No comfort band configured for this mode | Nothing |
| Comfort index inside the band | Nothing |

A room with no bands configured never actuates. There is no default to fall
back on, and inventing one would be worse than doing nothing.

## Direction

The controller first works out which way the room needs to move:

- Index above the band high → **cool**
- Index below the band low → **heat**
- Inside → nothing to do
- In precool, above the band low → **cool**, because precool targets the low
  bound rather than the middle

Direction is published in the trace as `demand`.

## 1. Covers

Free, and they work both ways: block solar gain when the room is too warm, admit
it when the room is too cold.

Skipped when:

- The room has no covers configured
- There is no illuminance reading, so there is no way to tell whether the room
  is sunlit
- Illuminance is below the solar gain threshold — moving a blind at night
  achieves nothing but noise

Cover movement is delegated to Adaptive Cover Pro, which handles sun geometry,
venetian dual-axis sequencing and glare zones. This controller sets intent only.

## 2. Fan

Air movement, no compressor. Tried only when the room is within 0.5 HCI of the
band. Beyond that a fan is noise, and the trace says how far out of band the
room actually was.

**Heating skips fan entirely.** A fan does not add heat.

## 3. Dry

A latent-dominated load costs far less to shift with dry mode on a low fan than
with cooling. Selected when indoor humidity is at or above 65%.

**Heating skips dry entirely.** Dry mode does not add heat.

## 4. Compressor

Reached only when everything above has been ruled out. For heating, covers are
the only cheaper step, so heating goes covers → compressor.

## The thresholds

| Threshold | Value | Status |
|---|---|---|
| Fan margin | 0.5 HCI | Fixed internal |
| Dry mode humidity | 65% | **Placeholder** |
| Solar gain | 2000 lux | **Placeholder** |

These are not settings and will not become settings. Exposing tuning parameters
is how configuration becomes unusable.

The two marked placeholder are stand-ins for the thermal model:

- A single humidity threshold cannot distinguish a latent load from a sensible
  one. 65% at 22 °C and 65% at 30 °C are different loads. The model learns the
  sensible and latent terms separately, and that split will make this decision
  properly.
- The lux threshold cannot be universal at all, because what a sensor reads
  depends on where in the room it sits and what it faces. The correct signal is
  solar gain predicted from sun position, aspect and an irradiance forecast.
  Until then it is a crude proxy and **will be wrong in rooms whose sensor is
  not near the window.**
