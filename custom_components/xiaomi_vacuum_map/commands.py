"""The commands that are sent to the robot of Xiaomi.

This robot does not have one way of going to a place. It has several,
and they do not all work on every firmware. So the ways are tried one
after the other and the one that the robot takes is used.

The room cleaning is the way that is known to work on this model. The
zone cleaning is tried as well, because it cleans only the block that
is picked, but the robot of some models only says that it understood
the command and then stays where it is.
"""

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .geometry import MapGeometry

_LOGGER = logging.getLogger(__name__)

# The service of Xiaomi that sends a command to the robot.
MIOT_DOMAIN = "xiaomi_miot"

# The service of the robot that has the commands of the vacuum.
VACUUM_SIID = 2

# The action that sets the zone that is going to be cleaned.
ACTION_SET_ZONE = 9
# The property that holds the zone.
PROP_ZONE_IDS = 10
# The action that starts the cleaning of the rooms that are given.
ACTION_ROOM_SWEEP = 13
# The property that holds the rooms that are going to be cleaned.
PROP_ROOM_IDS = 13
# The action of the extend service that starts a sweep that is not a
# room and not a zone, but a place that is picked on the map.
ACTION_CUSTOM_SWEEP = 7
EXTEND_SIID = 6


class CommandError(Exception):
    """The robot did not take the command."""


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any]) -> Any:
    """Call a service of the integration xiaomi_miot."""
    try:
        return await hass.services.async_call(
            MIOT_DOMAIN, service, data, blocking=True, return_response=True
        )
    except Exception as err:  # pylint: disable=broad-except
        raise CommandError(f"the service {service} failed: {err}") from err


async def clean_rooms(hass: HomeAssistant, entity_id: str,
                      room_ids: list[int]) -> list[str]:
    """Let the robot clean the given rooms.

    This is the way of cleaning that is known to work on this model.
    The rooms are given as the numbers that the robot uses for them.
    """
    if not room_ids:
        raise CommandError("no room was picked")

    # The robot wants the rooms in the text of a list. Two ways of
    # writing that list are in use, so both are tried.
    payloads = [
        json.dumps({"room": list(room_ids)}, separators=(",", ":")),
        json.dumps(list(room_ids), separators=(",", ":")),
    ]
    problems = []
    for payload in payloads:
        try:
            await _call(hass, "call_action", {
                "entity_id": entity_id,
                "siid": VACUUM_SIID,
                "aiid": ACTION_ROOM_SWEEP,
                "params": [{"piid": PROP_ROOM_IDS, "value": payload}],
            })
        except CommandError as err:
            problems.append(str(err))
            continue
        _LOGGER.info("the robot is asked to clean the rooms %s", room_ids)
        return [f"the rooms {room_ids} are cleaned"]

    raise CommandError("; ".join(problems) or "the robot refused")


async def clean_zone(hass: HomeAssistant, entity_id: str,
                     corners: list[int], repeats: int = 1) -> list[str]:
    """Let the robot clean the block of the map that is given.

    The corners are in millimetres of the house. The robot is told
    about the block first, and then it is told to start.
    """
    zone = {"blocks_region": list(corners), "blocks_attr": int(repeats)}
    payload = json.dumps(zone, separators=(",", ":"))

    try:
        await _call(hass, "set_miot_property", {
            "entity_id": entity_id,
            "siid": VACUUM_SIID,
            "piid": PROP_ZONE_IDS,
            "value": payload,
        })
    except CommandError as err:
        raise CommandError(f"the zone could not be set: {err}") from err

    for siid, aiid, piid in (
        (VACUUM_SIID, ACTION_SET_ZONE, PROP_ZONE_IDS),
        (VACUUM_SIID, ACTION_ROOM_SWEEP, PROP_ZONE_IDS),
        (EXTEND_SIID, ACTION_CUSTOM_SWEEP, None),
    ):
        params = [{"piid": piid, "value": payload}] if piid else []
        try:
            await _call(hass, "call_action", {
                "entity_id": entity_id,
                "siid": siid,
                "aiid": aiid,
                "params": params,
            })
        except CommandError:
            continue
        _LOGGER.info("the robot is asked to clean the zone %s", corners)
        return [f"the zone {corners} is cleaned"]

    raise CommandError("the robot did not start the zone")


async def go_to_point(hass: HomeAssistant, entity_id: str,
                      point: list[int]) -> list[str]:
    """Ask the robot to go to a place on the map.

    Not every model of this robot can go to a place without cleaning
    it. The command is sent and it is up to the robot to take it.
    """
    payload = json.dumps(list(point), separators=(",", ":"))
    problems = []
    for siid, aiid, piid in (
        (VACUUM_SIID, ACTION_CUSTOM_SWEEP, PROP_ZONE_IDS),
        (EXTEND_SIID, ACTION_CUSTOM_SWEEP, None),
    ):
        params = [{"piid": piid, "value": payload}] if piid else []
        try:
            await _call(hass, "call_action", {
                "entity_id": entity_id,
                "siid": siid,
                "aiid": aiid,
                "params": params,
            })
        except CommandError as err:
            problems.append(str(err))
            continue
        return [f"the robot is going to {point}"]
    raise CommandError("; ".join(problems) or "the robot refused")


def rooms_of_rect(geometry: MapGeometry, grid: bytes,
                  col1: int, row1: int, col2: int, row2: int) -> list[int]:
    """The rooms that a block of the map touches.

    The rooms are counted, so that the room that is mostly in the
    block comes first.
    """
    counts: dict[int, int] = {}
    for row in range(min(row1, row2), max(row1, row2) + 1):
        for col in range(min(col1, col2), max(col1, col2) + 1):
            room_id = geometry.room_id_of_cell(grid, col, row)
            if room_id is None:
                continue
            counts[room_id] = counts.get(room_id, 0) + 1
    return [room for room, _ in sorted(counts.items(), key=lambda i: -i[1])]
