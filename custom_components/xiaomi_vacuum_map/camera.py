"""The camera that shows the map of the robot."""

import json
import logging
import threading
import time

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
    MAP_OBJECTS,
)
from .map_renderer import draw

_LOGGER = logging.getLogger(__name__)


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
    camera.start_refresh()
    # The place of the robot is taken from the vacuum of xiaomi_miot.
    # That vacuum is looked for again later, because the place of the
    # robot only shows up after the first update from the cloud.
    hass.bus.async_listen(EVENT_STATE_CHANGED, camera._on_state_changed)


def _find_vacuum(hass: HomeAssistant, did: str) -> str | None:
    """Find the vacuum of xiaomi_miot that belongs to the given robot.

    The integration xiaomi_miot keeps the place of the robot in the state
    of the vacuum, so the map does not have to ask the cloud for it.
    """
    for state in hass.states.async_all("vacuum"):
        attributes = state.attributes
        # The map of the robot is named after the robot itself.
        name = str(attributes.get("vacuum_map.map_obj_name", ""))
        if did and did not in name:
            continue
        if attributes.get("vacuum_map.vacuum_position") is not None:
            return state.entity_id
    return None


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
        self._lock = threading.Lock()
        self._busy = False
        self._vacuum_entity: str | None = None
        self._position: list | None = None

    @property
    def extra_state_attributes(self):
        """The details of the map."""
        return {
            "map_object": self._data.get("map"),
            "did": self._data.get("did"),
            "last_fetch": self._last_fetch,
            "has_picture": self._image is not None,
            "robot_position": self._position,
        }

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
            name = str(state.attributes.get("vacuum_map.map_obj_name", ""))
            if did and did not in name:
                continue
            try:
                value = json.loads(raw)
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

    def _on_state_changed(self, event) -> None:
        """Keep the place of the robot up to date."""
        entity_id = event.data.get("entity_id")
        if not entity_id or not entity_id.startswith("vacuum."):
            return
        before = list(self._position) if self._position else None
        position = self._read_position()
        if position is None or position == before:
            return
        # The picture has to be drawn again with the new place.
        self.start_refresh()

    def _fetch(self) -> bytes | None:
        """Read the map from the cloud and draw it."""
        cloud = self._data["cloud"]
        did = self._data.get("did") or DEFAULT_DID

        map_data = None
        # The first map that is there is the one that is used.
        for obj_name in (self._data.get("map"), *MAP_OBJECTS):
            if not obj_name:
                continue
            map_data = cloud.get_map(obj_name)
            if map_data:
                break
        if not map_data:
            _LOGGER.warning("the map of the robot could not be read")
            return None

        position = self._read_position()
        if position is None:
            # The place of the robot is asked from the cloud itself.
            position = cloud.get_position(did)
        try:
            picture = draw(map_data, position)
        except (ValueError, KeyError, TypeError) as err:
            _LOGGER.error("the map of the robot could not be drawn: %s", err)
            return None
        _LOGGER.info("the map of the robot is read, %s bytes, robot at %s",
                     len(picture), position)
        return picture

    def refresh(self) -> None:
        """Fetch the map again and keep the picture."""
        with self._lock:
            if self._busy:
                return
            self._busy = True
        try:
            image = self._fetch()
        finally:
            with self._lock:
                self._busy = False
        if image:
            with self._lock:
                self._image = image
                self._last_fetch = time.time()
            # The picture is fetched in a thread, so the state of the
            # entity has to be written from the event loop.
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except RuntimeError:
                # Home Assistant is shutting down.
                pass

    def start_refresh(self) -> None:
        """Fetch the map in the background."""
        threading.Thread(target=self.refresh, daemon=True).start()

    def camera_image(self, width: int | None = None,
                    height: int | None = None) -> bytes | None:
        """Give the picture of the map.

        The picture is fetched in the background, so this call is quick.
        """
        with self._lock:
            image = self._image
            last_fetch = self._last_fetch
        if image is None:
            self.start_refresh()
            return _placeholder()
        if time.time() - last_fetch > DEFAULT_SCAN_INTERVAL:
            self.start_refresh()
        return image

    async def async_camera_image(self, width: int | None = None,
                                 height: int | None = None) -> bytes | None:
        """Give the picture of the map without waiting for a worker.

        The picture is fetched in the background, so this call gives the
        picture that is there right away. The workers of Home Assistant
        are often busy with the cameras that talk to a device, and a
        picture that waits for a worker would come too late.
        """
        with self._lock:
            image = self._image
            last_fetch = self._last_fetch
        if image is None:
            self.start_refresh()
            return _placeholder()
        if time.time() - last_fetch > DEFAULT_SCAN_INTERVAL:
            self.start_refresh()
        return image
