# Configuration

## The rule

**A setting exists only if a user cannot get a correct result without it.**

Anything derivable is derived. Anything that only tunes internals is fixed in
code or learned. This is not minimalism for its own sake: a configuration
reference running to dozens of individually defensible options is collectively
unusable, and that failure is what this project was started to avoid.

Applied consistently, it means the entire comfort configuration is one number
pair per room per mode.

## What you configure

### The entry

Nothing. Creating the integration takes no settings. One instance only.

### Per room

| Setting | Required | Effect if absent |
|---|---|---|
| Room name | Yes | — |
| Air conditioner | Yes | — |
| Temperature sensor | No | No comfort index, no actuation |
| Humidity sensor | No | No comfort index, no actuation |
| Presence sensor | No | Presence reads unknown; room holds occupied |
| Sleep schedule | No | Sleep band is never used |
| Which way the windows face | No | Blinds are never used, because the controller cannot tell when the sun is on the glass |
| Eave or soffit depth | No | No overhang shading; a window under an eave is treated as fully exposed |
| Eave height above the glass | No | As above; both measurements are needed together |
| Sun-on-window sensor | No | Overrides the direction, for a room too complex for one compass point |
| Illuminance sensor | No | Recorded only; not acted on |
| Heat source in the room | No | Equipment heat is not counted toward comfort |
| Fan or air movement | No | Falls back to whether the air conditioner is running |
| Windows and doors | No | No opening interlock |
| Blinds | No | Covers are never used |
| Wait before starting | Seeded | 2 minutes. Filters out grab-and-go visits |
| Wait before stopping | Seeded | 10 minutes. The answering-the-door allowance |
| Warn before shutting down | Seeded | 3 minutes between the two announcements |
| Announce before shutting down | No | Off. A house should not start talking uninvited |
| Announce through | No | Media players. Without any, nothing is announced |
| Lockout | No | Room actuates normally |

The room id is derived from the room name. Adding a room whose name produces an
existing id replaces that room rather than duplicating it.

**Lockout is a single dropdown.** The first option, "Not locked out", is the
default; choosing any other option is what switches lockout on. One field, one
screen, and no free text box that could be filled in by accident.

The list offers six built-in reasons and accepts one you type. A reason you type
is stored for the whole installation and offered for every room from then on.

### Comfort bands

One low and one high, per mode, per room, in HCI.

**Every room is seeded with the same defaults**, so a fresh install is sensible
with no configuration at all:

| Mode | Seeded band |
|---|---|
| Occupied | 24 – 27 |
| Sleep | 21 – 24 |
| Precool | 24 – 27 |

They arrive prefilled in the form and are meant to be changed. They come from
the ASHRAE 55 comfort zone converted onto the comfort index scale — nothing
about any particular house is seeded.

| Mode | Configurable |
|---|---|
| Occupied | Yes |
| Sleep | Yes |
| Precool | Yes |
| Unoccupied | **No — an unoccupied room is off** |
| Precondition | **No — uses the occupied band** |
| Coast | **No — uses the band of the mode it displaced** |
| Lockout | **No — never actuates** |

Clear a mode's bounds and the room is never actuated in that mode — an office
with no sleeping hours should clear the sleep pair. Both bounds must be supplied
together; a low at or above its high is rejected at setup.

**There is no global setting and no inheritance.** Every room is configured
independently. Inheritance is what makes configuration feel complicated, and it
removes no controls.

### Tariff windows

A window is a start, an end, a rate label, a set of constraints and whether
coasting is permitted. Added and removed one at a time from **Configure**. See
[Tariff](tariff.md).

Configuring no tariff is valid. The controller runs on comfort alone.

## What you do not configure, and why

| Not exposed | Why |
|---|---|
| Setpoints | Derived from the band and measured humidity |
| Regulation thresholds | Belong to the regulation layer, which is not this |
| Thermal model parameters | Learned |
| Fan margin, dry threshold, solar gain threshold | Internal tuning. See [Actuator ordering](actuator-ordering.md) |
| Evaluation interval | Internal |
| Hysteresis | Learned, with a fixed fallback |
| Which actuator to use | That is the decision you installed this to make |
| Occupancy grace defaults | Seeded per room and editable, because the right value depends on the room |

If one of these turns out to be something a user genuinely cannot work without,
that is a bug in the derivation, and the fix is better derivation rather than
another setting.

## Seeing what is configured

Two places, neither of which requires opening a form:

**The Configure menu** lists every room and every setting on it, the whole
tariff, and the house-wide settings, before you choose anything.

**Each room's device** carries a **Settings** sensor whose attributes are that
room's entire configuration, with anything unset stated as "Nothing selected"
rather than left blank.

The house-wide settings are sensors on the **Abode HVAC Coordinator** device.

## Changing configuration

Any change reloads the integration. Entities are recreated and the mode is
re-evaluated immediately. Learned model state survives a reload; it is keyed by
room id, so renaming a room to a new id starts its learning again.
