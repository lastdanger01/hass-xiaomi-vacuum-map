"""The services that send the robot of Xiaomi to a place.

A card that shows the map of the robot calls these services when a
place on the map is picked. The place is given in the pixels of the
picture of the map, so that the card does not have to know anything
about the millimetres of the house. The place is turned into the
millimetres here, with the same numbers that the picture was drawn
with, so the robot goes to the place that was picked.
"""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .commands import CommandError, clean_rooms, clean_zone, go_to_point, rooms_of_rect
from .const import DOMAIN, MAP_SCALE
from .geometry import MapGeometry

_LOGGER = logging.getLogger(__name__)

ATTR_ENTITY_ID = "entity_id"
ATTR_VACUUM = "vacuum"
ATTR_X = "x"
ATTR_Y = "y"
ATTR_X2 = "x2"
ATTR_Y2 = "y2"
ATTR_ROOM_ID = "room_id"
ATTR_REPEATS = "repeats"

# The service that cleans the room that is picked on the map.
SERVICE_ROOM = "clean_room"
# The service that cleans the block of the map that is picked.
SERVICE_ZONE = "clean_zone"
# The service that sends the robot to the place that is picked.
SERVICE_POINT = "go_to_point"

ROOM_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    vol.Optional(ATTR_ROOM_ID): vol.Coerce(int),
    vol.Optional(ATTR_REPEATS, default=1): vol.Coerce(int),
})

ZONE_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    vol.Required(ATTR_X): vol.Coerce(float),
    vol.Required(ATTR_Y): vol.Coerce(float),
    vol.Optional(ATTR_X2): vol.Coerce(float),
    vol.Optional(ATTR_Y2): vol.Coerce(float),
    vol.Optional(ATTR_REPEATS, default=1): vol.Coerce(int),
})

POINT_SCHEMA = vol.Schema({
    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
    vol.Required(ATTR_X): vol.Coerce(float),
    vol.Required(ATTR_Y): vol.Coerce(float),
})


def _camera_of(hass: HomeAssistant, entity_id: str):
    """The camera of the map that the entity id belongs to."""
    for data in hass.data.get(DOMAIN, {}).values():
        camera = data.get("camera")
        if camera is not None and camera.entity_id == entity_id:
            return camera
    return None


def _geometry_of(camera) -> MapGeometry | None:
    """The place of every cell of the map of the camera."""
    return camera.geometry if camera is not None else None


def _robot_of(camera) -> str:
    """The vacuum of xiaomi_miot that the commands are sent to.

    The commands cannot be sent without it, so the trouble is told
    plainly, instead of the service of xiaomi_miot complaining about
    an address that is not there.
    """
    entity_id = camera.vacuum_entity_id if camera is not None else None
    if not entity_id:
        raise CommandError(
            "the robot of the map is not known yet, so the command cannot "
            "be sent to it")
    return entity_id


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the services of the integration."""
    if hass.services.has_service(DOMAIN, SERVICE_ROOM):
        return

    async def _room(call: ServiceCall) -> None:
        """Clean the room that is picked."""
        camera = _camera_of(hass, call.data[ATTR_ENTITY_ID])
        geometry = _geometry_of(camera)
        if geometry is None or camera is None:
            raise CommandError("the map of the robot is not known yet")
        room_id = call.data.get(ATTR_ROOM_ID)
        if room_id is None:
            raise CommandError("no room was picked")
        await clean_rooms(hass, _robot_of(camera), [room_id],
                          int(call.data.get(ATTR_REPEATS, 1)))

    async def _zone(call: ServiceCall) -> None:
        """Clean the block of the map that is picked."""
        camera = _camera_of(hass, call.data[ATTR_ENTITY_ID])
        geometry = _geometry_of(camera)
        if geometry is None or camera is None:
            raise CommandError("the map of the robot is not known yet")

        first = geometry.cell_of_pixel(call.data[ATTR_X], call.data[ATTR_Y])
        if first is None:
            raise CommandError("the place that is picked is not on the map")
        col1, row1 = first
        x2 = call.data.get(ATTR_X2)
        y2 = call.data.get(ATTR_Y2)
        if x2 is None or y2 is None:
            col2, row2 = col1, row1
        else:
            second = geometry.cell_of_pixel(x2, y2)
            if second is None:
                raise CommandError("the place that is picked is not on the map")
            col2, row2 = second

        # The corners are taken from the places in the picture, so that
        # the block is the block that was picked and not the block of
        # whole cells around it.
        corners = geometry.pixel_rect_to_mm(
            call.data[ATTR_X], call.data[ATTR_Y], x2 or call.data[ATTR_X],
            y2 or call.data[ATTR_Y])
        repeats = int(call.data.get(ATTR_REPEATS, 1))

        # The robot of this model cleans the rooms that it knows best.
        # So the block is turned into the rooms that are in it, and the
        # robot is asked to clean those. The zone itself is asked for
        # as well, for the models that do take it.
        rooms = rooms_of_rect(geometry, camera.grid, col1, row1, col2, row2)
        try:
            await clean_zone(hass, _robot_of(camera), corners, repeats)
        except CommandError:
            if not rooms:
                raise
            await clean_rooms(hass, _robot_of(camera), rooms, repeats)

    async def _point(call: ServiceCall) -> None:
        """Send the robot to the place that is picked."""
        camera = _camera_of(hass, call.data[ATTR_ENTITY_ID])
        geometry = _geometry_of(camera)
        if geometry is None or camera is None:
            raise CommandError("the map of the robot is not known yet")
        cell = geometry.cell_of_pixel(call.data[ATTR_X], call.data[ATTR_Y])
        if cell is None:
            raise CommandError("the place that is picked is not on the map")
        x, y = geometry.mm_of_pixel(call.data[ATTR_X], call.data[ATTR_Y])
        await go_to_point(hass, _robot_of(camera), [x, y])

    hass.services.async_register(DOMAIN, SERVICE_ROOM, _room,
                                 schema=ROOM_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ZONE, _zone,
                                 schema=ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_POINT, _point,
                                 schema=POINT_SCHEMA)


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Take the services of the integration away."""
    for service in (SERVICE_ROOM, SERVICE_ZONE, SERVICE_POINT):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
