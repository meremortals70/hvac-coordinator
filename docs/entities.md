# Entities

Each room is a **device**. Three entities sit under it.

## `sensor.<room>_mode`

The current mode, and the whole reason behind it.

- **State:** one of `lockout`, `unoccupied`, `occupied`, `sleep`,
  `precondition`, `precool`, `coast`
- **Device class:** enum
- **Always available** once the room has been evaluated

Attributes:

| Attribute | Meaning |
|---|---|
| `room_id` | The room's id, for use in actions |
| `evaluated_at` | When this decision was made |
| `mode` | Same as the state |
| `base_mode` | The mode coast displaced, when coasting |
| `hci` | Comfort index at evaluation time |
| `band_low`, `band_high` | The band in force |
| `band_position` | `below`, `within` or `above` |
| `target_dry_bulb_c` | What the air conditioner would be asked for |
| `demand` | `cool`, `heat`, or none |
| `actuator` | `none`, `covers`, `fan`, `dry` or `compressor` |
| `reasons` | Why it is doing this |
| `rejected` | What was considered and ruled out, with why |
| `model` | The room's learned coefficients, their variance, sample count and whether each has converged |
| `hci_air_only` | The index before the radiant, still-air and heat-load corrections |
| `radiant_fraction` | How much solar load is reaching the room, 0 to 1 |

**`reasons` and `rejected` are the point of this integration.** Between them
they explain every decision, including which cheaper actuators were skipped and
on what grounds.

## `sensor.<room>_comfort_index`

- **State:** the comfort index, one decimal
- **Unit:** `HCI` — deliberately not `°C`
- **State class:** measurement, so it is recorded and graphable
- **Unavailable** when the temperature or humidity sensor has no reading

Attributes: `band_low`, `band_high`, `band_position`.

## `sensor.<room>_target_dry_bulb`

The dry bulb setpoint derived from the band and the measured humidity.

- **State:** temperature in °C
- **Device class:** temperature
- **Category:** diagnostic
- **Unavailable** when there is no band or no humidity reading

This is what the air conditioner would be asked for. Watching it against the
comfort index is the clearest way to see the humidity correction working: the
same band produces a lower setpoint on a humid night.

## `sensor.hvac_coordinator_demand_forecast`

One per installation, not per room. Projected HVAC energy over the next eight
hours, in kWh, with a per-window and per-room breakdown in its attributes.

This is the published contract with whatever owns the battery. **It carries no
vendor concepts.** See [Demand forecast](demand-forecast.md).

## The coordinator device

Everything house-wide appears as a sensor on a single **HVAC Coordinator**
device, so a setting you entered once is visible without reopening the form
that set it.

| Sensor | Shows |
|---|---|
| Demand forecast | Projected kWh over the horizon, with per-window and per-room breakdown |
| Tariff rate | The rate label in force now; the whole schedule in its attributes |
| Import price | Cents per kWh in force now |
| Feed-in price | Cents per kWh you are paid now; all export windows in its attributes |
| Daily supply charge | The fixed daily charge |
| Active constraints | Which constraints apply in the current window |
| Projected cost | The forecast energy priced per window, in dollars |
| Outdoor temperature | The configured outdoor feed |
| Rooms configured | How many, and which |

## Repair issues

| Issue | Raised when |
|---|---|
| Rooms with no comfort bands | A room has no bands and no lockout reason, so it can never actuate |
| Tariff constraints not acted on | A declared constraint is for another system to consume |

Neither is an error. Both exist so that a room quietly doing nothing is visible
rather than mysterious.

## Diagnostics

**Device page → three-dot menu → Download diagnostics.**

Contains every room's configuration, the tariff, unrecognised constraints, and
the current decision trace for every room. Entity IDs are included: they are how
your configuration is identified, and a diagnostics download without them cannot
explain a wrong decision.
