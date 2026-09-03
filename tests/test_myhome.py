import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITIES,
    CONF_FILE_PATH,
    CONF_FIRMWARE,
    CONF_GENERATE_EVENTS,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_PLATFORMS,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    CONF_WORKER_COUNT,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.validate import config_schema

MAC = "00:03:50:00:00:01"


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "myhome.yaml"
    path.write_text(
        """
gateway:
  mac: 00:03:50:00:00:01
  light:
    kitchen:
      where: '11'
      name: Kitchen
  cover:
    shutter:
      where: '12'
      name: Shutter
  sensor:
    room:
      where: '1'
      name: Room
      class: temperature
"""
    )
    return path


@pytest.fixture
def entry(config_file):
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_SSDP_LOCATION: None,
            CONF_SSDP_ST: None,
            CONF_DEVICE_TYPE: None,
            "friendly_name": None,
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_MANUFACTURER_URL: "http://www.bticino.it",
            CONF_NAME: "F454",
            CONF_FIRMWARE: None,
            CONF_MAC: MAC,
            CONF_UDN: None,
        },
        options={
            CONF_FILE_PATH: str(config_file),
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
        },
    )


@pytest.fixture
def gateway_mocks():
    async def pending(*_args):
        await asyncio.Event().wait()

    test = AsyncMock(return_value={"Success": True})
    with (
        patch.object(MyHOMEGatewayHandler, "test", test),
        patch.object(MyHOMEGatewayHandler, "listening_loop", pending),
        patch.object(MyHOMEGatewayHandler, "sending_loop", pending),
    ):
        yield test


async def test_setup_multiple_platforms(hass, entry, gateway_mocks):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    platforms = hass.data[DOMAIN][MAC][CONF_PLATFORMS]
    assert set(platforms) == {"light", "cover", "sensor", "button"}
    assert all(
        CONF_ENTITIES in device
        for devices in platforms.values()
        for device in devices.values()
    )
    unique_ids = {
        item.unique_id
        for item in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    assert {
        f"{MAC}-1-11",
        f"{MAC}-2-12",
        f"{MAC}-4-1-temperature",
        f"{MAC}-1-11-disable",
        f"{MAC}-1-11-enable",
    } <= unique_ids


async def test_setup_single_platform(hass, entry, gateway_mocks, config_file):
    config_file.write_text(
        """
gateway:
  mac: 00:03:50:00:00:01
  light:
    kitchen:
      where: '11'
      name: Kitchen
"""
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert set(hass.data[DOMAIN][MAC][CONF_PLATFORMS]) == {"light", "button"}


async def test_reload_and_unload(hass, entry, gateway_mocks):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert MAC not in hass.data[DOMAIN]
    assert not entry._background_tasks


async def test_options_flow_reloads_once(hass, entry, gateway_mocks, config_file):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "address": "192.0.2.2",
            "password": "54321",
            CONF_FILE_PATH: str(config_file),
            CONF_WORKER_COUNT: 2,
            CONF_GENERATE_EVENTS: True,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.data[CONF_HOST] == "192.0.2.2"
    assert entry.options[CONF_WORKER_COUNT] == 2
    assert entry.state is ConfigEntryState.LOADED
    assert gateway_mocks.await_count == 2


async def test_failed_platform_setup_cleans_runtime(hass, entry):
    entry.add_to_hass(hass)
    with (
        patch.object(MyHOMEGatewayHandler, "test", AsyncMock(return_value={"Success": True})),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(side_effect=RuntimeError("setup failed")),
        ),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ) as unload,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert MAC not in hass.data[DOMAIN]
    unload.assert_awaited_once()
    assert not entry._background_tasks


def test_schema_initializes_runtime_fields_before_entities():
    config = config_schema(
        {
            "gateway": {
                CONF_MAC: MAC,
                "light": {"alias": {"where": "11", "name": "Kitchen"}},
                "cover": {"alias": {"where": "12", "name": "Shutter"}},
                "sensor": {
                    "alias": {
                        "where": "1",
                        "name": "Room",
                        "class": "temperature",
                    }
                },
                "climate": {"alias": {}},
            }
        }
    )[MAC][CONF_PLATFORMS]

    assert "1-11" in config["light"]
    assert config["light"]["1-11"]["icon"] is None
    assert config["cover"]["2-12"]["entity_name"] is None
    assert config["sensor"]["4-1"][CONF_ENTITIES] == {"temperature": None}
    assert config["climate"]["4-#0"][CONF_ENTITIES] == {"climate": None}
    assert config["button"]["1-11"] is not config["light"]["1-11"]
    assert (
        config["button"]["1-11"][CONF_ENTITIES]
        is not config["light"]["1-11"][CONF_ENTITIES]
    )
