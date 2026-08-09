"""Constants.

No site data lives here. Tariff windows, comfort bands and entity IDs are
configuration, entered at setup. A fresh install starts empty.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Final

DOMAIN: Final = "hvac_coordinator"
LOGGER: Final = logging.getLogger(__package__)

#: Floor on how often rooms are re-evaluated. Evaluation is also driven by
#: state changes on the entities each room reads, so this is a backstop.
EVALUATION_INTERVAL: Final = timedelta(seconds=30)

CONF_ROOMS: Final = "rooms"
CONF_ROOM_ID: Final = "room_id"
CONF_CLIMATE_ENTITY: Final = "climate_entity_id"
CONF_TEMPERATURE_ENTITY: Final = "temperature_entity_id"
CONF_HUMIDITY_ENTITY: Final = "humidity_entity_id"
CONF_PRESENCE_ENTITY: Final = "presence_entity_id"
CONF_SLEEP_SCHEDULE_ENTITY: Final = "sleep_schedule_entity_id"
CONF_ILLUMINANCE_ENTITY: Final = "illuminance_entity_id"
CONF_OPENING_ENTITIES: Final = "opening_entity_ids"
CONF_COVER_ENTITIES: Final = "cover_entity_ids"
CONF_LOCKOUT_REASON: Final = "lockout_reason"
CONF_BANDS: Final = "bands"
CONF_BAND_LOW: Final = "low"
CONF_BAND_HIGH: Final = "high"
CONF_TARIFF_WINDOWS: Final = "tariff_windows"
CONF_START: Final = "start"
CONF_END: Final = "end"
CONF_RATE: Final = "rate"
CONF_CONSTRAINTS: Final = "constraints"
CONF_COASTING_PERMITTED: Final = "coasting_permitted"

SERVICE_HEADING_HOME: Final = "heading_home"
SERVICE_CLEAR_OVERRIDE: Final = "clear_override"
ATTR_ROOM_ID: Final = "room_id"
ATTR_DEADLINE: Final = "deadline"

ISSUE_UNRECOGNISED_CONSTRAINT: Final = "unrecognised_constraint"
ISSUE_NO_BANDS: Final = "room_without_bands"
