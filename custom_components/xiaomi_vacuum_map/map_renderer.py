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

# The colours of the robot on the map. The body is light like the top
# of the robot, the edge is dark and the front is red, so the way the
# robot faces can be seen.
ROBOT_BODY = (252, 252, 250)
ROBOT_EDGE = (44, 46, 50)
ROBOT_FRONT = (214, 44, 44)
ROBOT_TURRET = (96, 100, 106)


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

    The kind of the point says what it is: 0 is a point of the way to
    the room and of the way back to the dock, 1 is a point of the path
    that was really swept.
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
        # The grid of the robot starts at the bottom of the house and
        # goes up, but the first row of a picture is the top of it, so
        # the rows are put in the picture the other way round.
        y0 = (height - 1 - row) * scale
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
            painter.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill=color)

    origin_x = float(map_data.get("origin_x", 0))
    origin_y = float(map_data.get("origin_y", 0))
    resolution = float(map_data.get("resolution", 50) or 50)

    def cell_of(x: Any, y: Any) -> tuple[int, int] | None:
        """The cell of the map of the given place in the house.

        The row is the row of the grid of the robot, which starts at the
        bottom of the house.
        """
        if x is None or y is None:
            return None
        col = int(round((float(x) - origin_x) / resolution))
        row = int(round((float(y) - origin_y) / resolution))
        if not (0 <= row < height and 0 <= col < width):
            return None
        return col, row

    def pixel_of(cell: tuple[int, int]) -> tuple[int, int]:
        """The place in the picture of a cell of the grid."""
        col, row = cell
        return col * scale + scale // 2, (height - 1 - row) * scale + scale // 2

    def mark(x: Any, y: Any, color: tuple[int, int, int], name: str) -> None:
        cell = cell_of(x, y)
        if cell is None:
            return
        cx, cy = pixel_of(cell)
        radius = 3 * scale
        painter.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                        fill=color, outline=(255, 255, 255), width=1)

    def draw_robot(x: Any, y: Any, heading: float | None) -> None:
        """Draw the robot as a round body that faces the way it drives.

        The body is a light circle with a dark edge, like the robot seen
        from above. The front of it is red, and the little turret on
        top of it is grey, so the place of the robot and the way it
        faces can both be seen on the map.
        """
        import math

        cell = cell_of(x, y)
        if cell is None:
            return
        cx, cy = pixel_of(cell)
        radius = max(4, 3 * scale)
        # The angle of the picture runs the other way than the angle of
        # the house, so the sign of the angle is turned around.
        angle = math.radians(-(heading or 0.0))
        cos_a, sin_a = math.cos(angle), math.sin(angle)

        def point(dx: float, dy: float) -> tuple[float, float]:
            """A place on the robot, in the picture."""
            return (cx + dx * cos_a - dy * sin_a,
                    cy + dx * sin_a + dy * cos_a)

        painter.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                        fill=ROBOT_BODY, outline=ROBOT_EDGE,
                        width=max(1, radius // 3))
        # The bumper of the robot is at the front of it.
        front = point(radius * 0.34, 0)
        side_a = point(radius * 0.34, -radius * 0.62)
        side_b = point(radius * 0.34, radius * 0.62)
        painter.line([side_a, front, side_b], fill=ROBOT_FRONT,
                     width=max(1, radius // 4), joint="curve")
        # The turret with the laser sits a little behind the middle.
        turret = point(-radius * 0.16, 0)
        tr = max(1, radius // 3)
        painter.ellipse([turret[0] - tr, turret[1] - tr,
                         turret[0] + tr, turret[1] + tr],
                        fill=ROBOT_TURRET)

    # The path that the robot drove is drawn as a line of the points of
    # it. The part that was really swept is in colour, the way to the
    # room and the way back to the dock is grey.
    if path:
        cells = [cell_of(x, y) for _, x, y in path]
        for index in range(len(path) - 1):
            start = cells[index]
            end = cells[index + 1]
            if start is None or end is None:
                continue
            color = PATH if path[index][0] == 1 else PATH_DONE
            painter.line(
                [pixel_of(start), pixel_of(end)],
                fill=color, width=max(1, scale),
            )

    mark(map_data.get("pile_x"), map_data.get("pile_y"), DOCK, "dock")
    if position and len(position) >= 2:
        heading = position[2] if len(position) > 2 else None
        draw_robot(position[0], position[1], heading)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
