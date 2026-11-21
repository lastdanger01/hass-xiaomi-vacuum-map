"""The camera that shows the map of the robot."""

import asyncio
import json
import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DID,
    CONF_MAP,
    DEFAULT_DID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LIVE_SCAN_INTERVAL,
    MAP_OBJECTS,
    MAP_SCALE,
    WATCH_INTERVAL,
)
from .geometry import MapGeometry
from .map_renderer import draw, unpack_grid, unpack_path

_LOGGER = logging.getLogger(__name__)

# The states of the vacuum that mean that the robot is on the move.
ACTIVE_STATES = ("cleaning", "returning")

# The states of the sensor of the work of the robot that mean the same.
ACTIVE_SENSOR_STATES = ("sweeping", "mopping")


def _obj_name(value: Any) -> str:
    """Take the name of a map out of what the vacuum of xiaomi_miot gives.

    The name of the map was a plain string before, but it is a dict with
    the name and the number of the map in it now. Both are understood.
    """
    if isinstance(value, dict):
        value = value.get("obj_name") or value.get("obj") or ""
    if not value:
        return ""
    return str(value)


def _placeholder() -> bytes:
    """A picture that is shown while the map is being fetched."""
    from PIL import Image, ImageDraw
    import io

    image = Image.new("RGB", (640, 400), (247, 247, 245))
    painter = ImageDraw.Draw(image)
    painter.rectangle([0, 0, 639, 399], outline=(200, 200, 196), width=2)
    painter.text((20, 190), "The map of the robot is being fetched",
                fill=(120, 120, 118))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    """Set up the camera of the map."""
    data = hass.data[DOMAIN][entry.entry_id]
    camera = XiaomiVacuumMapCamera(hass, data, entry)
    async_add_entities([camera])
    # The map is read from the cloud, that takes longer than the ten
    # seconds that Home Assistant gives for a picture. So the picture is
    # fetched in the background, before it is asked for.
    await camera.async_start()
    # The place of the robot is taken from the vacuum of xiaomi_miot.
    # That vacuum is looked for again later, because the place of the
    # robot only shows up after the first update from the cloud.
    hass.bus.async_listen(EVENT_STATE_CHANGED, camera._on_state_changed)
    # The camera is put in the data of the entry, so that the services
    # can find it when a place on the map is picked.
    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})[
        "camera"] = camera


def _find_vacuum(hass: HomeAssistant, did: str) -> str | None:
    """Find the vacuum of xiaomi_miot that belongs to the given robot.

    The integration xiaomi_miot keeps the place of the robot in the state
    of the vacuum, so the map does not have to ask the cloud for it.
    """
    fallback = None
    for state in hass.states.async_all("vacuum"):
        attributes = state.attributes
        # The map of the robot is named after the robot itself.
        name = _obj_name(attributes.get("vacuum_map.map_obj_name"))
        if did and did not in name:
            continue
        if attributes.get("vacuum_map.vacuum_position") is not None:
            return state.entity_id
        if fallback is None:
            fallback = state.entity_id
    return fallback


class XiaomiVacuumMapCamera(Camera):
    """The map of the robot, drawn from the cloud of Xiaomi."""

    _attr_should_poll = False
    _attr_supported_features = CameraEntityFeature(0)
    _attr_name = "Vacuum map"

    def __init__(self, hass: HomeAssistant, data: dict, entry: ConfigEntry) -> None:
        super().__init__()
        self.hass = hass
        self._data = data
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{data.get('did', DEFAULT_DID)}"
        self.content_type = "image/png"
        self._image: bytes | None = None
        self._last_fetch = 0.0
        self._busy = False
        self._vacuum_entity: str | None = None
        self._position: list | None = None
        self._map_object: str | None = None
        self._trajectory_object: str | None = None
        self._moving = False
        self._task: asyncio.Task | None = None
        self._unsub: Any = None
        self._map_data: dict | None = None
        self._grid: bytes | None = None

    @property
    def extra_state_attributes(self):
        """The details of the map."""
        attributes = {
            "map_object": self._data.get("map"),
            "did": self._data.get("did"),
            "last_fetch": self._last_fetch,
            "has_picture": self._image is not None,
            "robot_position": self._position,
            "live_map_object": self._map_object,
            "trajectory_object": self._trajectory_object,
            "moving": self._moving,
        }
        # The size of the map and the place of every cell of it. A card
        # needs this to turn a place that is picked into the place in
        # the house that the robot knows.
        if self._map_data is not None:
            attributes["map_geometry"] = MapGeometry(
                self._map_data, MAP_SCALE).as_dict()
            attributes["rooms"] = self._rooms()
        return attributes

    def _rooms(self) -> list[dict]:
        """The rooms of the map, with the number that the robot uses."""
        if self._map_data is None:
            return []
        geometry = MapGeometry(self._map_data, MAP_SCALE)
        rooms = []
        for grid_id, room_id in geometry.room_id_by_grid.items():
            rooms.append({
                "grid_id": grid_id,
                "room_id": room_id,
                "color": geometry.room_color_by_grid.get(grid_id, 0),
            })
        return sorted(rooms, key=lambda room: room["room_id"])

    @property
    def geometry(self) -> MapGeometry | None:
        """The place of every cell of the map."""
        if self._map_data is None:
            return None
        return MapGeometry(self._map_data, MAP_SCALE)

    @property
    def grid(self) -> bytes | None:
        """The grid of the map, one byte for every cell."""
        return self._grid

    @property
    def vacuum_entity_id(self) -> str | None:
        """The vacuum of xiaomi_miot that belongs to this robot.

        The commands to the robot are sent to that vacuum, because it
        holds the account and the way to the robot.
        """
        return self._vacuum_entity

    def set_vacuum_entity(self, entity_id: str) -> None:
        """Watch the vacuum of xiaomi_miot for the place of the robot.

        The integration xiaomi_miot already asks the cloud for the place
        of the robot, and keeps it in the state of the vacuum. Reading it
        from there is easier and gives the same answer.
        """
        self._vacuum_entity = entity_id
        self._read_position()

    def _read_position(self) -> list | None:
        """Take the place of the robot from the state of the vacuum.

        The vacuum is looked for again every time, because the place of
        the robot only shows up in the state after the first update from
        the cloud.
        """
        did = self._data.get("did") or ""
        candidates = []
        if self._vacuum_entity:
            state = self.hass.states.get(self._vacuum_entity)
            if state is not None:
                candidates.append(state)
        for state in self.hass.states.async_all("vacuum"):
            if state not in candidates:
                candidates.append(state)

        fallback = None
        for state in candidates:
            raw = state.attributes.get("vacuum_map.vacuum_position")
            if not raw:
                continue
            # The map of the robot is named after the robot itself.
            name = _obj_name(state.attributes.get("vacuum_map.map_obj_name"))
            if did and did not in name:
                continue
            value = raw
            if isinstance(value, (str, bytes)):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    continue
            position = value.get("position") if isinstance(value, dict) else value
            if not isinstance(position, (list, tuple)) or len(position) < 2:
                continue
            if self._vacuum_entity != state.entity_id:
                self._vacuum_entity = state.entity_id
            if not isinstance(position, list):
                position = list(position)
            if position != self._position:
                _LOGGER.info("the robot is at %s", position)
            self._position = position
            return self._position
        return fallback

    def _read_map_object(self) -> str | None:
        """Take the name of the map of the robot from the vacuum.

        While the robot cleans, it writes a map of its own. The name of
        that map changes with every cleaning, so it is taken from the
        vacuum of xiaomi_miot instead of being written down here.
        """
        did = self._data.get("did") or ""
        candidates = []
        if self._vacuum_entity:
            state = self.hass.states.get(self._vacuum_entity)
            if state is not None:
                candidates.append(state)
        for state in self.hass.states.async_all("vacuum"):
            if state not in candidates:
                candidates.append(state)
        for state in candidates:
            name = _obj_name(state.attributes.get("vacuum_map.map_obj_name"))
            if not name:
                continue
            if did and did not in name:
                continue
            return name
        return None

    def _read_trajectory_object(self) -> str | None:
        """Take the name of the file with the path of the robot.

        The integration xiaomi_miot keeps it in the state of the vacuum,
        as the name of the object of the trajectory. That name is better
        than guessing it, because the number of the path does not have
        to be the one right after the number of the map.
        """
        did = self._data.get("did") or ""
        candidates = []
        if self._vacuum_entity:
            state = self.hass.states.get(self._vacuum_entity)
            if state is not None:
                candidates.append(state)
        for state in self.hass.states.async_all("vacuum"):
            if state not in candidates:
                candidates.append(state)
        for state in candidates:
            name = _obj_name(state.attributes.get("vacuum_map.trajectory_obj_name"))
            if not name:
                continue
            if did and did not in name:
                continue
            return name
        return None

    def _on_state_changed(self, event) -> None:
        """Keep the place of the robot up to date."""
        entity_id = event.data.get("entity_id")
        if not entity_id or not entity_id.startswith("vacuum."):
            return
        before = list(self._position) if self._position else None
        position = self._read_position()
        if position is None or position == before:
            return
        # The picture has to be drawn again with the new place. The
        # event comes from a thread, so the work is put on the loop of
        # the events of Home Assistant.
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_refresh())
        )

    def _read_path(self, cloud) -> list[tuple[int, int, int]] | None:
        """Read the path that the robot drove from the cloud.

        The path is in a file of its own, the one right after the map of
        the robot in the cloud of Xiaomi.
        """
        name = self._trajectory_object or self._read_trajectory_object()
        if name:
            content = cloud.get_file(name)
            if content:
                points = unpack_path(content)
                if points:
                    self._trajectory_object = name
                    _LOGGER.info("the path of the robot is read, %s points",
                                 len(points))
                    return points
        # The name of the trajectory is not always there. The path is
        # then the file right after the map of the robot.
        name = self._map_object or self._read_map_object()
        if not name:
            return None
        parts = name.split("/")
        if len(parts) < 3:
            return None
        try:
            number = int(parts[-1])
        except ValueError:
            return None
        path_name = "/".join(parts[:-1] + [str(number + 1)])
        content = cloud.get_file(path_name)
        if not content:
            return None
        points = unpack_path(content)
        if not points:
            return None
        self._trajectory_object = path_name
        _LOGGER.info("the path of the robot is read, %s points", len(points))
        return points

    def _fetch_map(self) -> dict | None:
        """Read the map of the robot from the cloud.

        The map does not change while the robot drives, so it is only
        read again when the robot writes a new one. The path of the
        robot is a file of its own and changes all the time.
        """
        cloud = self._data["cloud"]
        did = self._data.get("did") or DEFAULT_DID

        map_data = None
        # The map of the robot that is cleaning now comes first, then
        # the map that is written down.
        live_object = self._read_map_object()
        names = []
        if live_object:
            names.append(live_object)
        for obj_name in (self._data.get("map"), *MAP_OBJECTS):
            if obj_name and obj_name not in names:
                names.append(obj_name)
        for obj_name in names:
            map_data = cloud.get_map(obj_name)
            if map_data:
                self._map_object = obj_name
                break
        if not map_data:
            _LOGGER.warning("the map of the robot could not be read")
            return None
        return map_data

    def _fetch(self) -> bytes | None:
        """Read the map from the cloud and draw it."""
        cloud = self._data["cloud"]
        did = self._data.get("did") or DEFAULT_DID

        map_data = self._fetch_map()
        if not map_data:
            return None
        self._map_data = map_data
        try:
            self._grid = unpack_grid(map_data)
        except (ValueError, KeyError):
            self._grid = None

        position = self._read_position()

        # The path that the robot drove is a file of its own, right
        # after the map of the robot.
        path = self._read_path(cloud)

        # The last point of the path is where the robot is right now.
        # The place of the robot in the state of the vacuum is only
        # changed now and then, so it is often the place of a moment
        # ago. The path is read all the time, so it is much fresher.
        # The path has no direction in it, so the way the robot faces
        # is taken from the state of the vacuum.
        if path:
            heading = position[2] if position and len(position) > 2 else None
            position = [path[-1][1], path[-1][2]]
            if heading is not None:
                position.append(heading)

        try:
            picture = draw(map_data, position, path=path)
        except (ValueError, KeyError, TypeError) as err:
            _LOGGER.error("the map of the robot could not be drawn: %s", err)
            return None
        _LOGGER.info("the map of the robot is read, %s bytes, robot at %s",
                     len(picture), position)
        return picture

    async def async_refresh(self) -> None:
        """Fetch the map again and keep the picture."""
        if self._busy:
            return
        self._busy = True
        try:
            # The call to the cloud is slow, so it does not hold up the
            # loop of the events of Home Assistant.
            image = await self.hass.async_add_executor_job(self._fetch)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("the map of the robot could not be fetched")
            image = None
        finally:
            self._busy = False
        if image:
            self._image = image
            self._last_fetch = _now()
            self.async_write_ha_state()

    def _robot_is_moving(self) -> bool:
        """Tell whether the robot is on the move.

        While the robot drives, the path that it drove changes all the
        time. The map is then fetched much more often than when the
        robot stands still.
        """
        for state in self.hass.states.async_all("vacuum"):
            if state.state in ACTIVE_STATES:
                return True
        for state in self.hass.states.async_all("sensor"):
            if state.state in ACTIVE_SENSOR_STATES:
                return True
        return False

    async def _watch(self) -> None:
        """Keep the map up to date in the background.

        Without this, the map is only fetched when somebody looks at
        it, and then only once every five minutes. The path of the
        robot is then much too late, because the robot drives on.

        The map is fetched every few seconds, also when the robot
        stands still. The place of the robot in the picture is taken
        from the state of the vacuum of xiaomi_miot, and that state
        only changes now and then. So the picture is only drawn again
        when something really changed, but the path of the robot is
        read all the time.
        """
        # The map is fetched every two seconds. The time that the fetch
        # takes is taken off the wait, so the period stays the same even
        # when the fetch is slow.
        deadline = _now()
        while True:
            try:
                moving = self._robot_is_moving()
                self._moving = moving
                if self._image is not None and _now() >= deadline:
                    started = _now()
                    await self.async_refresh()
                    # The time that the fetch took is part of the period,
                    # so the picture comes every two seconds and not every
                    # two seconds plus the time of the fetch.
                    deadline = started + LIVE_SCAN_INTERVAL
            except asyncio.CancelledError:
                raise
            except Exception:  # pylint: disable=broad-except
                # The watcher must not stop when something goes wrong
                # once.
                _LOGGER.exception("the watcher of the map failed")
            wait = deadline - _now()
            if wait > 0:
                await asyncio.sleep(wait)
            else:
                await asyncio.sleep(0.1)

    async def async_start(self) -> None:
        """Start the watcher that keeps the map up to date."""
        # The vacuum of xiaomi_miot is looked for now, so that a command
        # to the robot can be sent even when the place of the robot is
        # not known yet.
        if self._vacuum_entity is None:
            found = _find_vacuum(self.hass, self._data.get("did") or "")
            if found:
                self._vacuum_entity = found
        await self.async_refresh()
        self._task = self.hass.async_create_task(self._watch())

    async def async_will_remove_from_hass(self) -> None:
        """Stop the watcher when the camera goes away."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def camera_image(self, width: int | None = None,
                     height: int | None = None) -> bytes | None:
        """Give the picture of the map.

        The picture is fetched in the background, so this call is quick.
        """
        if self._image is None:
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(self.async_refresh())
            )
            return _placeholder()
        if _now() - self._last_fetch > LIVE_SCAN_INTERVAL:
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(self.async_refresh())
            )
        return self._image

    async def async_camera_image(self, width: int | None = None,
                                 height: int | None = None) -> bytes | None:
        """Give the picture of the map without waiting for a worker.

        The picture is fetched in the background, so this call gives the
        picture that is there right away. The workers of Home Assistant
        are often busy with the cameras that talk to a device, and a
        picture that waits for a worker would come too late.
        """
        if self._image is None:
            self.hass.async_create_task(self.async_refresh())
            return _placeholder()
        if _now() - self._last_fetch > LIVE_SCAN_INTERVAL:
            self.hass.async_create_task(self.async_refresh())
        return self._image


def _now() -> float:
    """The time of now, in seconds."""
    import time
    return time.time()
