"""Fixtures for the Home Assistant side tests.

NOT YET RUN. Requires pytest-homeassistant-custom-component.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hvac_coordinator.const import (
    CONF_BANDS,
    CONF_CLIMATE_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_PRESENCE_ENTITY,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_TARIFF_WINDOWS,
    CONF_TEMPERATURE_ENTITY,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of the custom integration in every test."""


@pytest.fixture
def mock_setup_entry() -> Generator[None]:
    """Stop the entry actually setting up during config flow tests."""
    with patch(
        "custom_components.hvac_coordinator.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture
def room_config() -> dict:
    """A single room. Fixture values only — no site data."""
    return {
        CONF_ROOM_ID: "test_room",
        "name": "Test Room",
        CONF_CLIMATE_ENTITY: "climate.test",
        CONF_TEMPERATURE_ENTITY: "sensor.test_temperature",
        CONF_HUMIDITY_ENTITY: "sensor.test_humidity",
        CONF_PRESENCE_ENTITY: "binary_sensor.test_presence",
        CONF_BANDS: {"occupied": {"low": 25.0, "high": 28.0}},
    }


@pytest.fixture
def mock_config_entry(room_config: dict) -> MockConfigEntry:
    """A loaded config entry with one room."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="HVAC Coordinator",
        data={CONF_ROOMS: [], CONF_TARIFF_WINDOWS: []},
        options={CONF_ROOMS: [room_config], CONF_TARIFF_WINDOWS: []},
    )
