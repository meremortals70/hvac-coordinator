# HVAC Coordinator

A Home Assistant integration that decides what your air conditioning should be
doing, room by room, and tells you why.

It holds each room to a comfort band, works through the cheap actuators before
the expensive ones, respects your tariff's constraints as absolute rules, and
publishes a full decision trace so nothing it does is a mystery.

**It never writes to your battery.**

> **Status: v0.3.0. Never installed, never run in Home Assistant.**
> The decision logic is written and unit tested. Actuation is not yet wired —
> see [Known limitations](docs/known-limitations.md).

---

## What it does

You tell it how a room should *feel*. It works out what to ask the air
conditioner for.

That is the whole idea. You never set a setpoint. You set one comfort band per
room per mode, and the controller derives the dry bulb target from that band and
the measured humidity — so the same band produces a different setpoint on a
humid night than a dry one.

Everything else follows from four rules:

1. **Comfort is the constraint, not the variable.** Cost never narrows the band.
2. **An unoccupied room is off.** Not a wider band. Off.
3. **Cheapest actuator first.** Covers, then fan, then dry, then compressor —
   and every step skipped is recorded with the reason.
4. **Tariff constraints are absolute.** They are never traded against comfort or
   price at runtime.

## Documentation

| Document | What is in it |
|---|---|
| [Use cases](docs/use-cases.md) | What this is for, and what it is not for |
| [Installation](docs/installation.md) | Installing, configuring and removing |
| [Configuration](docs/configuration.md) | Every setting, and why there are so few |
| [Comfort index](docs/comfort-index.md) | The index, the maths, and the band table |
| [Modes](docs/modes.md) | The seven modes and their precedence |
| [Actuator ordering](docs/actuator-ordering.md) | Cheapest first, in detail |
| [Tariff](docs/tariff.md) | Windows, constraints, and what they mean |
| [Actions](docs/actions.md) | The two services, with examples |
| [Entities](docs/entities.md) | What it creates and what each one reports |
| [Data updates](docs/data-updates.md) | When it evaluates and what triggers it |
| [Examples](docs/examples.md) | Working automations |
| [Troubleshooting](docs/troubleshooting.md) | When it does something you did not expect |
| [Known limitations](docs/known-limitations.md) | What is not built, stated plainly |
| [Architecture](docs/architecture.md) | How the three layers fit together |
| [Contributing](docs/contributing.md) | Running the tests, the quality scale |
| [Publishing](GITHUB-UPLOAD.md) | Getting this onto GitHub via the web UI |

## Quick start

1. Copy `custom_components/hvac_coordinator/` into your Home Assistant
   `config/custom_components/` directory
2. Restart Home Assistant
3. **Settings → Devices & Services → Add Integration → HVAC Coordinator**
4. **Configure** to add your first room

Full detail in [Installation](docs/installation.md).

## Requirements

- Home Assistant 2026.6.0 or later
- A `climate` entity per room
- Temperature and humidity sensors for each room you want controlled

Nothing else is required. Presence, illuminance, openings, covers and a sleep
schedule each unlock behaviour if present, and are optional if not.

## Licence

Apache 2.0. See [ATTRIBUTION.md](ATTRIBUTION.md) for what this project borrows
and from where.
