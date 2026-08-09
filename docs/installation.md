# Installation

## Requirements

- Home Assistant **2026.6.0** or later
- One `climate` entity per room you want controlled
- A temperature sensor and a humidity sensor per room

No external dependencies. The integration makes no network calls and installs no
Python packages.

## Install

### Manual

1. Download or clone this repository
2. Copy the folder `custom_components/hvac_coordinator/` into your Home
   Assistant configuration directory, so you end up with
   `config/custom_components/hvac_coordinator/manifest.json`
3. Restart Home Assistant

### HACS

1. HACS → three-dot menu → **Custom repositories**
2. Add this repository's URL, category **Integration**
3. Install **HVAC Coordinator**
4. Restart Home Assistant

## Set up

1. **Settings → Devices & Services → Add Integration**
2. Search for **HVAC Coordinator** and select it
3. Submit the empty form

That creates the coordinator. It has no rooms yet and does nothing.

**Only one instance can be created.** A second attempt aborts with "already
configured". Rooms live inside the single entry.

## Add a room

**Settings → Devices & Services → HVAC Coordinator → Configure**

Two steps. First the room and its entities:

| Field | Required | What it does |
|---|---|---|
| Room name | Yes | Becomes the device name. The room id is derived from it |
| Air conditioner | Yes | The `climate` entity for this room |
| Temperature sensor | No | Without it, no comfort index and no actuation |
| Humidity sensor | No | Without it, no comfort index and no actuation |
| Presence sensor | No | Without it, presence reads unknown and the room holds occupied |
| Sleep schedule | No | Without it, the sleep band is never used |
| Illuminance sensor | No | Without it, covers are never used |
| Windows and doors | No | Any one open suspends the room |
| Blinds | No | Without any, covers are never used |
| Lockout reason | No | Any text here means the room never actuates |

Then the comfort bands, in HCI. See [Comfort index](comfort-index.md) for what
the numbers mean and where to start.

Repeat Configure for each room. Adding a room with a name that slugs to an
existing room id replaces that room.

## Verify it is working

Each room becomes a **device** with three entities under it. Check
**Settings → Devices & Services → HVAC Coordinator → devices**:

- `sensor.<room>_mode` — should show a mode, not `unknown`
- `sensor.<room>_comfort_index` — should show a number
- `sensor.<room>_target_dry_bulb` — should show a temperature

Open the mode sensor's attributes. The `reasons` and `rejected` lists explain
the current decision. If they do not make sense, that is a bug worth reporting.

## Remove

**Settings → Devices & Services → HVAC Coordinator → three-dot menu → Delete**

This removes the config entry, every room device, every entity, and the stored
thermal model state. Nothing is left behind in `.storage`.

To remove a single room rather than everything, delete its device from the
device page. The delete button is enabled only once the room is no longer in
the configuration.

To uninstall completely, delete the entry first, then remove
`custom_components/hvac_coordinator/` and restart.
