"""The commands that are sent to the robot of Xiaomi.

The robot of this house was watched while it was told to clean a block
of the map by the app of the phone, and the way it was written down
was taken from that. So the zone is a dictionary with the corners of
the block in it, and nothing else. The way the cleaning is started is
to set the kind of cleaning first, and then to start it.
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
# The property that holds the places that are picked on the map. The
# robot reads it when it is told to clean a place of its own.
PROP_POINTS = 15
# The kind of cleaning that the robot is told to do. The robot of this
# house was seen with the kind 4, which is a block of the map.
SWEEP_TYPE_PROP = 4
# The value of the kind of cleaning that is a block of the map.
SWEEP_TYPE_AREA = 4
# The action that starts the cleaning. It is needed as well, because
# the robot does not start on its own when only the zone is set.
ACTION_START_SWEEP = 1
# The action of the extend service that starts a sweep that is not a
# room and not a zone, but a place that is picked on the map.
ACTION_CUSTOM_SWEEP = 7
EXTEND_SIID = 6


class CommandError(Exception):
    """The robot did not take the command."""


async def _call(hass: HomeAssistant, service: str, data: dict[str, Any]) -> Any:
    """Call a service of the integration xiaomi_miot.

    The services of xiaomi_miot do not raise when they fail. They
    give back the trouble in the answer instead. So the answer is
    looked at as well, or a failure would be taken for a success.
    """
    try:
        answer = await hass.services.async_call(
            MIOT_DOMAIN, service, data, blocking=True, return_response=True
        )
    except Exception as err:  # pylint: disable=broad-except
        raise CommandError(f"the service {service} failed: {err}") from err

    problem = _problem_of(answer)
    if problem:
        raise CommandError(f"the service {service} failed: {problem}")
    return answer


def _problem_of(answer: Any) -> str:
    """The trouble that the answer of xiaomi_miot holds, if any.

    The answer is a list of the answers of the entities that were
    asked. Each of them is a dictionary. A failure is told by the key
    error, or by a code that is not zero.
    """
    answers = answer if isinstance(answer, list) else [answer]
    for item in answers:
        if not isinstance(item, dict):
            continue
        if error := item.get("error"):
            return str(error)
        result = item.get("result")
        if isinstance(result, dict) and result.get("code") not in (0, None):
            return json.dumps(result, ensure_ascii=False)
    return ""


async def clean_rooms(hass: HomeAssistant, entity_id: str,
                      room_ids: list[int],
                      repeats: int = 1) -> list[str]:
    """Let the robot clean the given rooms.

    This is the way of cleaning that is known to work on this model.
    The rooms are given as the numbers that the robot uses for them.
    The robot wants them in the text of a list, and the way of writing
    that list is not the same on every firmware, so a few ways are
    tried.
    """
    if not room_ids:
        raise CommandError("no room was picked")

    rooms = list(room_ids)
    payloads = [
        json.dumps([{"segments": rooms, "repeat": int(repeats)}],
                   separators=(",", ":")),
        json.dumps({"room": rooms, "repeat": int(repeats)},
                   separators=(",", ":")),
        json.dumps({"room": rooms}, separators=(",", ":")),
        json.dumps(rooms, separators=(",", ":")),
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
        _LOGGER.info("the robot is asked to clean the rooms %s", rooms)
        return [f"the rooms {rooms} are cleaned"]

    raise CommandError("; ".join(problems) or "the robot refused")


async def clean_zone(hass: HomeAssistant, entity_id: str,
                     corners: list[int], repeats: int = 1) -> list[str]:
    """Let the robot clean the block of the map that is given.

    The corners are in millimetres of the house. The block is given to
    the robot the way the app of the phone gives it: a dictionary with
    the corners in it and nothing else. The kind of cleaning is set
    first, because the robot only cleans the block when it knows that
    a block is wanted. Then the block is set, and then the cleaning is
    started.
    """
    zone = {"blocks_region": [int(corner) for corner in corners]}
    payload = json.dumps(zone, separators=(",", ":"))

    problems = []
    # The kind of cleaning is set first. The robot of this house does
    # not take this over the cloud, because the firmware of it holds the
    # kind of cleaning for itself. The cleaning is started all the same
    # when it does not take it.
    try:
        await _call(hass, "set_miot_property", {
            "entity_id": entity_id,
            "siid": VACUUM_SIID,
            "piid": SWEEP_TYPE_PROP,
            "value": SWEEP_TYPE_AREA,
        })
    except CommandError as err:
        problems.append(str(err))
        _LOGGER.info("the kind of cleaning could not be set: %s", err)

    # The block of the map that is going to be cleaned.
    try:
        await _call(hass, "call_action", {
            "entity_id": entity_id,
            "siid": VACUUM_SIID,
            "aiid": ACTION_SET_ZONE,
            "params": [{"piid": PROP_ZONE_IDS, "value": payload}],
        })
    except CommandError as err:
        problems.append(str(err))
        raise CommandError("; ".join(problems) or "the robot did not take the zone")

    # The cleaning is started. Without this the robot holds the block
    # and waits.
    try:
        await _call(hass, "call_action", {
            "entity_id": entity_id,
            "siid": VACUUM_SIID,
            "aiid": ACTION_START_SWEEP,
            "params": [],
        })
    except CommandError as err:
        problems.append(str(err))
        raise CommandError("; ".join(problems) or "the robot did not start")

    _LOGGER.info("the robot is asked to clean the zone %s", corners)
    return [f"the zone {corners} is cleaned"]


async def go_to_point(hass: HomeAssistant, entity_id: str,
                      point: list[int]) -> list[str]:
    """Ask the robot to go to a place on the map.

    The place is given in the millimetres of the house, the same ones
    that the robot uses to say where it is.

    The way that is used is the one of the extend service of the robot:
    the place is written down in the property of the places, and the
    robot is told to start a sweep that is not a room and not a block
    of the map. This is the way that the app of the phone uses when a
    place is picked on the map.
    """
    problems = []
    x, y = int(point[0]), int(point[1])

    places = json.dumps([[x, y]], separators=(",", ":"))
    try:
        await _call(hass, "set_miot_property", {
            "entity_id": entity_id,
            "siid": VACUUM_SIID,
            "piid": PROP_POINTS,
            "value": places,
        })
        await _call(hass, "call_action", {
            "entity_id": entity_id,
            "siid": EXTEND_SIID,
            "aiid": ACTION_CUSTOM_SWEEP,
            "params": [],
        })
        _LOGGER.info("the robot is asked to go to %s", point)
        return [f"the robot is going to {point}"]
    except CommandError as err:
        problems.append(str(err))

    # The way that is tried after that is a small block around the
    # place. The robot drives to the place and cleans that little bit.
    # It is only tried when the first way did not work, because the
    # robot of some firmwares cleans the whole house when the place
    # did not arrive.
    radius = 300
    corners = [x - radius, y - radius, x - radius, y + radius,
               x + radius, y + radius, x + radius, y - radius]
    try:
        await clean_zone(hass, entity_id, corners, 1)
        _LOGGER.info("the robot is asked to go to %s", point)
        return [f"the robot is going to {point}"]
    except CommandError as err:
        problems.append(str(err))

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
