"""The integration that shows the map of the robot of Xiaomi.

The map is read from the cloud of Xiaomi. The accounts of the
integration xiaomi_miot are used, so no name and no word have to be
given twice. There can be more than one of those accounts, so every
one of them is tried until the map is really there.

The robot itself is never started, only the map and the place of the
robot are read.
"""

import json
import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import (
    CONF_COUNTRY,
    CONF_DID,
    CONF_MAP,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_COUNTRY,
    DEFAULT_DID,
    DOMAIN,
    MAP_OBJECTS,
)
from .cloud import XiaomiCloud
from .services import async_register_services, async_unregister_services

# The card that lets the robot be sent to a place is a file of its own.
# It is served from here, so that it can be loaded by the browser.
CARD_URL = "/xiaomi_vacuum_map/xiaomi-vacuum-map-card.js"
CARD_FOLDER = "frontend"
CARD_FLAG = "xiaomi_vacuum_map_card_registered"

_LOGGER = logging.getLogger(__name__)

STORAGE = ".storage/core.config_entries"


def _find_accounts(hass: HomeAssistant, did: str = "") -> list[dict[str, str]]:
    """Find the accounts of the integration xiaomi_miot in the storage.

    The token of that integration is taken as it is, so the account
    does not have to be given a second time. The accounts that hold
    the robot of this house come first.
    """
    try:
        path = hass.config.path(STORAGE)
        with open(path) as handle:
            entries = json.load(handle)["data"]["entries"]
    except (OSError, ValueError, KeyError):
        return []

    found: list[dict[str, str]] = []
    for entry in entries:
        if entry.get("domain") != "xiaomi_miot":
            continue
        data = entry.get("data") or {}
        service_token = data.get("service_token")
        ssecurity = data.get("ssecurity")
        user_id = data.get("user_id")
        if not (service_token and ssecurity and user_id):
            continue
        account = {
            "username": data.get("username", ""),
            "password": data.get("password", ""),
            "country": data.get("server_country") or DEFAULT_COUNTRY,
            "user_id": user_id,
            "ssecurity": ssecurity,
            "service_token": service_token,
            "device_id": data.get("device_id", ""),
            "has_robot": did in (data.get("did_list") or []),
        }
        found.append(account)

    found.sort(key=lambda item: not item["has_robot"])
    return found


def _entry_account(entry: ConfigEntry) -> dict[str, str] | None:
    """The account that was given in the config entry."""
    account = {
        "username": entry.data.get(CONF_USERNAME, ""),
        "password": entry.data.get(CONF_PASSWORD, ""),
        "country": entry.data.get(CONF_COUNTRY) or DEFAULT_COUNTRY,
        "service_token": entry.data.get("service_token", ""),
        "ssecurity": entry.data.get("ssecurity", ""),
        "user_id": entry.data.get("user_id", ""),
        "device_id": entry.data.get("device_id", ""),
    }
    if not (account["service_token"] and account["ssecurity"]):
        return None
    return account


def _make_cloud(account: dict[str, str]) -> XiaomiCloud:
    """Make the connection to the cloud for the given account."""
    return XiaomiCloud(
        username=account.get("username", ""),
        password=account.get("password", ""),
        country=account.get("country") or DEFAULT_COUNTRY,
        service_token=account.get("service_token", ""),
        ssecurity=account.get("ssecurity", ""),
        user_id=account.get("user_id", ""),
        device_id=account.get("device_id", ""),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    await async_register_services(hass)
    await _register_card(hass)
    did = entry.data.get(CONF_DID) or DEFAULT_DID
    map_object = entry.data.get(CONF_MAP) or MAP_OBJECTS[0]

    accounts = await hass.async_add_executor_job(_find_accounts, hass, did)
    from_entry = _entry_account(entry)
    if from_entry:
        accounts.insert(0, from_entry)

    cloud = None
    for account in accounts:
        candidate = _make_cloud(account)
        # The token in the storage of Home Assistant is often too old
        # to be used, so the account is signed in as well. The sign in
        # is a slow call to the network, so it is done outside the loop
        # of the events.
        signed_in = await hass.async_add_executor_job(candidate.login)
        if not signed_in:
            _LOGGER.debug("the account %s could not be signed in",
                          account.get("username"))
            continue
        # The account is only used when the map is really there.
        map_data = await hass.async_add_executor_job(candidate.get_map, map_object)
        if map_data:
            _LOGGER.debug("the account %s gives the map of the robot",
                          account.get("username"))
            cloud = candidate
            break
        _LOGGER.debug("the account %s does not give the map",
                      account.get("username"))

    if cloud is None:
        _LOGGER.error("no account of the cloud of Xiaomi gives the map "
                      "of the robot")
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "cloud": cloud,
        "did": did,
        "map": map_object,
    }

    await hass.config_entries.async_forward_entry_setups(entry, [Platform.CAMERA])
    return True


async def _register_card(hass: HomeAssistant) -> None:
    """Let the browser get the card that shows the map.

    The card is a file of its own in the folder of the integration. It
    is served from here, so that it works without anything else being
    installed.
    """
    if CARD_FLAG in hass.data:
        return
    folder = hass.config.path(f"custom_components/{DOMAIN}/{CARD_FOLDER}")
    if not os.path.isdir(folder):
        return
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(f"/{DOMAIN}", folder, False)])
    except RuntimeError:
        # The path is already registered, that is all right.
        pass
    hass.data[CARD_FLAG] = True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, [Platform.CAMERA])
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            await async_unregister_services(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry, the map of the robot stays as it is."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
