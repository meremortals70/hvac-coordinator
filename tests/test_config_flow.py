"""Config and options flow. NOT YET RUN."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hvac_coordinator.const import (
    CONF_CLIMATE_ENTITY,
    CONF_ROOMS,
    DOMAIN,
)


async def test_user_flow_collects_the_first_room(
    hass: HomeAssistant, mock_setup_entry: None
) -> None:
    """Setup produces a working room rather than an empty hub."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "room"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "First Room", CONF_CLIMATE_ENTITY: "climate.first"},
    )
    assert result["step_id"] == "bands"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"occupied_low": 24.0, "occupied_high": 27.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(result["data"][CONF_ROOMS]) == 1
    assert result["data"][CONF_ROOMS][0]["room_id"] == "first_room"


async def test_user_flow_rejects_an_inverted_band(
    hass: HomeAssistant, mock_setup_entry: None
) -> None:
    """The same validation applies during initial setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "First Room", CONF_CLIMATE_ENTITY: "climate.first"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"occupied_low": 27.0, "occupied_high": 24.0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "band_inverted"}


async def test_single_instance_only(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A second entry is refused."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_adds_a_room(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A room is added across the room and bands steps."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "room"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Second Room", CONF_CLIMATE_ENTITY: "climate.second"},
    )
    assert result["step_id"] == "bands"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"occupied_low": 25.0, "occupied_high": 28.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(result["data"][CONF_ROOMS]) == 2


async def test_inverted_band_is_rejected(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A band whose low is above its high is refused."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Third Room", CONF_CLIMATE_ENTITY: "climate.third"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"occupied_low": 28.0, "occupied_high": 25.0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "band_inverted"}
