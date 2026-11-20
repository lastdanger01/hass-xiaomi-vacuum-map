"""Draw the map of the robot.

The cloud keeps the map of this model as a json file. In the field
map_data there is the grid of the map, one byte for every cell, packed
with zlib and coded in base64. This module unpacks it and draws the
picture of the map, with the rooms in their colours, the robot and the
dock on it.

The path that the robot drove is a file of its own. It is the text of a
base64 string, and inside of it is the zlib of the points. Every point
is five bytes: the kind of the point, and the place of it in the
millimetres of the house as two little endian numbers of two bytes.
"""

import base64
import io
import struct
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
PATH = (232, 90, 60)
PATH_DONE = (150, 150, 148)


def unpack_grid(map_data: dict[str, Any]) -> bytes:
    """Take the grid of the map out of the json."""
    raw = base64.b64decode(map_data["map_data"])
    return zlib.decompress(raw)


def unpack_path(content: bytes) -> list[tuple[int, int, int]]:
    """Take the points of the path of the robot out of the file.

    The file is the text of a base64 string, and inside of it is the
    zlib of the points. Every point is five bytes: the kind of the
    point, and the place of it as two little endian numbers of two
    bytes, in the millimetres of the house.

    The kind of the point says what it is: 0 is a point of the path
    that was driven, 1 is a point where the robot is now.
    """
    try:
        raw = base64.b64decode(content)
        raw = zlib.decompress(raw)
    except (ValueError, zlib.error):
        return []
    points = []
    for index in range(0, len(raw) - 4, 5):
        kind, x, y = struct.unpack_from("<Bhh", raw, index)
        points.append((kind, x, y))
    return points


def draw(map_data: dict[str, Any], position: list | None = None,
         scale: int = 2, path: list[tuple[int, int, int]] | None = None) -> bytes:
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

    def cell_of(x: Any, y: Any) -> tuple[int, int] | None:
        """The cell of the map of the given place in the house."""
        if x is None or y is None:
            return None
        col = int(round((float(x) - origin_x) / resolution))
        row = int(round((float(y) - origin_y) / resolution))
        if not (0 <= row < height and 0 <= col < width):
            return None
        return col, row

    def mark(x: Any, y: Any, color: tuple[int, int, int], name: str) -> None:
        cell = cell_of(x, y)
        if cell is None:
            return
        col, row = cell
        radius = 3 * scale
        cx = col * scale + scale // 2
        cy = row * scale + scale // 2
        painter.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                        fill=color, outline=(255, 255, 255), width=1)

    # The path that the robot drove is drawn as a line of the points of
    # it. The points of the path that is done are grey, the ones of the
    # path that is being driven now are in colour.
    if path:
        done = [(x, y) for kind, x, y in path if kind == 0]
        now = [(x, y) for kind, x, y in path if kind != 0]
        for points, color in ((done, PATH_DONE), (now, PATH)):
            cells = [cell_of(x, y) for x, y in points]
            cells = [c for c in cells if c is not None]
            if len(cells) < 2:
                continue
            painter.line(
                [(c[0] * scale + scale // 2, c[1] * scale + scale // 2)
                 for c in cells],
                fill=color, width=max(1, scale),
                joint="curve",
            )

    mark(map_data.get("pile_x"), map_data.get("pile_y"), DOCK, "dock")
    if position and len(position) >= 2:
        mark(position[0], position[1], ROBOT, "robot")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
