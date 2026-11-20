"""Draw the map of the robot.

The cloud keeps the map of this model as a json file. In the field
map_data there is the grid of the map, one byte for every cell, packed
with zlib and coded in base64. This module unpacks it and draws the
picture of the map, with the rooms in their colours, the robot and the
dock on it.
"""

import base64
import io
import zlib
from typing import Any

# The colours of the rooms, the key is the colour number of the cloud.
ROOM_COLORS: dict[int, tuple[int, int, int]] = {
    1: (222, 142, 62),
    2: (117, 176, 118),
    3: (108, 154, 210),
    4: (206, 121, 176),
    5: (196, 168, 106),
    6: (140, 118, 186),
}

FREE = (247, 247, 245)
WALL = (38, 40, 44)
OBSTACLE = (188, 188, 184)
DOCK = (30, 110, 220)
ROBOT = (220, 40, 40)


def unpack_grid(map_data: dict[str, Any]) -> bytes:
    """Take the grid of the map out of the json."""
    raw = base64.b64decode(map_data["map_data"])
    return zlib.decompress(raw)


def draw(map_data: dict[str, Any], position: list | None = None,
         scale: int = 2) -> bytes:
    """Draw the picture of the map and give it back as png bytes."""
    from PIL import Image, ImageDraw

    width = int(map_data["width"])
    height = int(map_data["height"])
    grid = unpack_grid(map_data)
    if len(grid) != width * height:
        raise ValueError(f"the grid is {len(grid)} bytes, the map is "
                         f"{width} x {height}")

    rooms: dict[int, tuple[int, int, int]] = {}
    for room in map_data.get("map_room_info") or []:
        color = ROOM_COLORS.get(int(room.get("color", 0)), (150, 150, 150))
        rooms[int(room["grid_id"])] = color

    image = Image.new("RGB", (width * scale, height * scale), FREE)
    painter = ImageDraw.Draw(image)
    for row in range(height):
        for col in range(width):
            value = grid[row * width + col]
            if value == 0:
                continue
            if value == 1:
                color = WALL
            elif value == 2:
                color = OBSTACLE
            else:
                color = rooms.get(value, (150, 150, 150))
            x0 = col * scale
            y0 = row * scale
            painter.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill=color)

    origin_x = float(map_data.get("origin_x", 0))
    origin_y = float(map_data.get("origin_y", 0))
    resolution = float(map_data.get("resolution", 50) or 50)

    def mark(x: Any, y: Any, color: tuple[int, int, int], name: str) -> None:
        if x is None or y is None:
            return
        col = int(round((float(x) - origin_x) / resolution))
        row = int(round((float(y) - origin_y) / resolution))
        if not (0 <= row < height and 0 <= col < width):
            return
        radius = 3 * scale
        cx = col * scale + scale // 2
        cy = row * scale + scale // 2
        painter.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                        fill=color, outline=(255, 255, 255), width=1)

    mark(map_data.get("pile_x"), map_data.get("pile_y"), DOCK, "dock")
    if position and len(position) >= 2:
        mark(position[0], position[1], ROBOT, "robot")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
