""" MyHOME integration. """

import aiofiles
import yaml

from OWNd.message import OWNCommand, OWNGatewayCommand

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er, config_validation as cv
from homeassistant.const import CONF_MAC

from .const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    CONF_PLATFORMS,
    CONF_ENTITY,
    CONF_ENTITIES,
    CONF_WORKER_COUNT,
    CONF_FILE_PATH,
    CONF_GENERATE_EVENTS,
    DOMAIN,
    LOGGER,
)
from .validate import config_schema, format_mac
from .gateway import MyHOMEGatewayHandler

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
def _coerce_str(value):
    """Coerce legacy tuple/list values (from older manual config entries) to a plain string.

    The device registry rejects non-string values; older config entries stored some
    fields (e.g. manufacturer) wrapped in a single-element tuple.
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value


async def async_setup(hass, config):
    """Set up the MyHOME component."""
    hass.data.setdefault(DOMAIN, {})

    if DOMAIN not in config:
        return True

    LOGGER.error("configuration.yaml not supported for this component!")

    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    if entry.data[CONF_MAC] not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.data[CONF_MAC]] = {}

    _config_file_path = (
        str(entry.options[CONF_FILE_PATH])
        if CONF_FILE_PATH in entry.options
        else "/config/myhome.yaml"
    )
    _generate_events = (
        entry.options[CONF_GENERATE_EVENTS]
        if CONF_GENERATE_EVENTS in entry.options
        else False
    )

    try:
        async with aiofiles.open(_config_file_path, mode="r") as yaml_file:
            _validated_config = config_schema(yaml.safe_load(await yaml_file.read()))
    except FileNotFoundError:
        LOGGER.error(f"Configartion file '{_config_file_path}' is not present!")
        hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
        return False

    if entry.data[CONF_MAC] in _validated_config:
        hass.data[DOMAIN][entry.data[CONF_MAC]] = _validated_config[
            entry.data[CONF_MAC]
        ]
    else:
        hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
        return False

    platforms = tuple(
        hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS]
    )

    # Migrating the config entry's unique_id if it was not formated to the recommended hass standard
    if entry.unique_id != dr.format_mac(entry.unique_id):
        hass.config_entries.async_update_entry(
            entry, unique_id=dr.format_mac(entry.unique_id)
        )
        LOGGER.warning("Migrating config entry unique_id to %s", entry.unique_id)

    gateway_handler = MyHOMEGatewayHandler(
        hass=hass, config_entry=entry, generate_events=_generate_events
    )
    hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY] = gateway_handler

    try:
        tests_results = await gateway_handler.test()
    except OSError as ose:
        hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
        raise ConfigEntryNotReady(
            f"Gateway cannot be reached at {gateway_handler.gateway.host}, make sure its address is correct."
        ) from ose

    if not tests_results["Success"]:
        if (
            tests_results["Message"] == "password_error"
            or tests_results["Message"] == "password_required"
        ):
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_REAUTH},
                    data=entry.data,
                )
            )
        hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
        return False

    _command_worker_count = (
        int(entry.options[CONF_WORKER_COUNT])
        if CONF_WORKER_COUNT in entry.options
        else 1
    )

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    gateway_device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])},
        identifiers={
            (DOMAIN, hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].unique_id)
        },
        manufacturer=_coerce_str(hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].manufacturer),
        name=_coerce_str(hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].name),
        model=_coerce_str(hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].model),
        sw_version=_coerce_str(hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_ENTITY].firmware),
    )
    gateway_handler.device_entry_id = gateway_device_entry.id

    # Pruning lose entities and devices from the registry
    entity_entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)

    entities_to_be_removed = []
    devices_to_be_removed = [
        device_entry.id
        for device_entry in dr.async_entries_for_config_entry(
            device_registry, entry.entry_id
        )
    ]

    configured_entities = []

    for _platform in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS].keys():
        for _device in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][
            _platform
        ].keys():
            for _entity_name in hass.data[DOMAIN][entry.data[CONF_MAC]][CONF_PLATFORMS][
                _platform
            ][_device][CONF_ENTITIES]:
                if _entity_name != _platform:
                    configured_entities.append(
                        f"{entry.data[CONF_MAC]}-{_device}-{_entity_name}"
                    )  # extrapolating _attr_unique_id out of the entity's place in the config data structure
                else:
                    configured_entities.append(
                        f"{entry.data[CONF_MAC]}-{_device}"
                    )  # extrapolating _attr_unique_id out of the entity's place in the config data structure

    for entity_entry in entity_entries:
        if entity_entry.unique_id in configured_entities:
            if entity_entry.device_id in devices_to_be_removed:
                devices_to_be_removed.remove(entity_entry.device_id)
            continue
        entities_to_be_removed.append(entity_entry.entity_id)

    for enity_id in entities_to_be_removed:
        entity_registry.async_remove(enity_id)

    if gateway_device_entry.id in devices_to_be_removed:
        devices_to_be_removed.remove(gateway_device_entry.id)

    for device_id in devices_to_be_removed:
        if (
            len(
                er.async_entries_for_device(
                    entity_registry, device_id, include_disabled_entities=True
                )
            )
            == 0
        ):
            device_registry.async_remove_device(device_id)

    # Defining the services
    async def handle_sync_time(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        if gateway is None:
            gateway = list(hass.data[DOMAIN].keys())[0]
        else:
            mac = format_mac(gateway)
            if mac is None:
                LOGGER.error(
                    "Invalid gateway mac `%s`, could not send time synchronisation message.",
                    gateway,
                )
                return False
            else:
                gateway = mac
        timezone = hass.config.as_dict()["time_zone"]
        if gateway in hass.data[DOMAIN]:
            await hass.data[DOMAIN][gateway][CONF_ENTITY].send(
                OWNGatewayCommand.set_datetime_to_now(timezone)
            )
        else:
            LOGGER.error(
                "Gateway `%s` not found, could not send time synchronisation message.",
                gateway,
            )
            return False

    async def handle_send_message(call):
        gateway = call.data.get(ATTR_GATEWAY, None)
        message = call.data.get(ATTR_MESSAGE, None)
        if gateway is None:
            gateway = list(hass.data[DOMAIN].keys())[0]
        else:
            mac = format_mac(gateway)
            if mac is None:
                LOGGER.error(
                    "Invalid gateway mac `%s`, could not send message `%s`.",
                    gateway,
                    message,
                )
                return False
            else:
                gateway = mac
        LOGGER.debug("Handling message `%s` to be sent to `%s`", message, gateway)
        if gateway in hass.data[DOMAIN]:
            if message is not None:
                own_message = OWNCommand.parse(message)
                if own_message is not None:
                    if own_message.is_valid:
                        LOGGER.debug(
                            "%s Sending valid OpenWebNet Message: `%s`",
                            hass.data[DOMAIN][gateway][CONF_ENTITY].log_id,
                            own_message,
                        )
                        await hass.data[DOMAIN][gateway][CONF_ENTITY].send(own_message)
                else:
                    LOGGER.error(
                        "Could not parse message `%s`, not sending it.", message
                    )
                    return False
        else:
            LOGGER.error(
                "Gateway `%s` not found, could not send message `%s`.", gateway, message
            )
            return False

    try:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
    except BaseException:
        await hass.config_entries.async_unload_platforms(entry, platforms)
        hass.data[DOMAIN].pop(entry.data[CONF_MAC], None)
        raise

    hass.services.async_register(DOMAIN, "sync_time", handle_sync_time)
    hass.services.async_register(DOMAIN, "send_message", handle_send_message)

    gateway_handler.listening_worker = entry.async_create_background_task(
        hass,
        gateway_handler.listening_loop(),
        f"{DOMAIN}-{entry.entry_id}-listener",
    )
    for i in range(_command_worker_count):
        gateway_handler.sending_workers.append(
            entry.async_create_background_task(
                hass,
                gateway_handler.sending_loop(i),
                f"{DOMAIN}-{entry.entry_id}-sender-{i}",
            )
        )

    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""

    LOGGER.info("Unloading MyHome entry.")

    entry_data = hass.data[DOMAIN][entry.data[CONF_MAC]]
    platforms = tuple(entry_data[CONF_PLATFORMS])
    gateway_handler = entry_data[CONF_ENTITY]
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if not unload_ok:
        return False

    await gateway_handler.close_listener()

    hass.services.async_remove(DOMAIN, "sync_time")
    hass.services.async_remove(DOMAIN, "send_message")

    del hass.data[DOMAIN][entry.data[CONF_MAC]]

    return True
