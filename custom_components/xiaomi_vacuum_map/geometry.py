"""The place of every point of the map of the robot.

The map of the robot is a grid of cells. Every cell has a place in
the house, in millimetres, and the origin of the grid is the lower
left corner of the map. The robot tells where it is in the same
millimetres, so the place that is picked on the map can be given to
the robot without guessing anything.

The picture of the map is drawn with a few pixels for every cell, so
the place on the picture and the place of the cell are not the same.
Both are worked out here, so that the picture and the robot always
agree.
"""

from typing import Any


class MapGeometry:
    """Where every cell of the map of the robot is in the house."""

    def __init__(self, map_data: dict[str, Any], scale: int = 4) -> None:
        self.width = int(map_data.get("width") or 0)
        self.height = int(map_data.get("height") or 0)
        self.origin_x = float(map_data.get("origin_x") or 0)
        self.origin_y = float(map_data.get("origin_y") or 0)
        self.resolution = float(map_data.get("resolution") or 50)
        self.scale = int(scale or 4)
        self.pile_x = map_data.get("pile_x")
        self.pile_y = map_data.get("pile_y")

        # The rooms of the map. Every room has a number in the grid of
        # the robot and a number of its own in the list of the rooms.
        self.room_id_by_grid: dict[int, int] = {}
        self.room_color_by_grid: dict[int, int] = {}
        for room in map_data.get("map_room_info") or []:
            try:
                grid_id = int(room["grid_id"])
                room_id = int(room["room_id"])
            except (KeyError, TypeError, ValueError):
                continue
            self.room_id_by_grid[grid_id] = room_id
            self.room_color_by_grid[grid_id] = int(room.get("color", 0) or 0)

    @property
    def image_width(self) -> int:
        """The width of the picture of the map, in pixels."""
        return self.width * self.scale

    @property
    def image_height(self) -> int:
        """The height of the picture of the map, in pixels."""
        return self.height * self.scale

    def cell_of(self, x: Any, y: Any) -> tuple[int, int] | None:
        """The cell of the map that holds the given place.

        The place is in the millimetres of the house, the same ones
        the robot uses to say where it is. The row of the grid of the
        robot starts at the bottom of the house, so the row that is
        given back is counted from there.
        """
        if x is None or y is None:
            return None
        try:
            col = int(round((float(x) - self.origin_x) / self.resolution))
            row = int(round((float(y) - self.origin_y) / self.resolution))
        except (TypeError, ValueError):
            return None
        if not (0 <= row < self.height and 0 <= col < self.width):
            return None
        return col, row

    def mm_of_cell(self, col: int, row: int) -> tuple[int, int]:
        """The place in the house of the middle of a cell."""
        x = int(self.origin_x + (col + 0.5) * self.resolution)
        y = int(self.origin_y + (row + 0.5) * self.resolution)
        return x, y

    def cell_of_pixel(self, px: float, py: float) -> tuple[int, int] | None:
        """The cell of the map that is shown at the given place of the picture.

        The place is in the pixels of the picture as it is drawn, not
        in the pixels that the picture happens to have on the screen.
        """
        if self.scale <= 0:
            return None
        col = int(px // self.scale)
        # The first row of a picture is the top of the house, the first
        # row of the grid of the robot is the bottom of it.
        row = self.height - 1 - int(py // self.scale)
        if not (0 <= row < self.height and 0 <= col < self.width):
            return None
        return col, row

    def pixel_of_cell(self, col: int, row: int) -> tuple[int, int]:
        """The place in the picture of the middle of a cell.

        The rows of the picture start at the top, the rows of the grid
        of the robot start at the bottom of the house.
        """
        return (col * self.scale + self.scale // 2,
                (self.height - 1 - row) * self.scale + self.scale // 2)

    def cell_rect_to_mm(self, col1: int, row1: int,
                        col2: int, row2: int) -> list[int]:
        """The corners of a block of cells, in millimetres.

        The corners are given the way the robot wants them: the first
        corner, the second corner over the first one, the corner
        opposite the first one and the last corner.
        """
        x1, y1 = self.mm_of_cell(min(col1, col2), min(row1, row2))
        x2, y2 = self.mm_of_cell(max(col1, col2), max(row1, row2))
        return [x1, y1, x1, y2, x2, y2, x2, y1]

    def room_id_of_cell(self, grid: bytes, col: int, row: int) -> int | None:
        """The number of the room that a cell belongs to.

        The grid holds the number of the room in every cell that is
        part of a room. The cells that are not in a room have no room
        of their own.
        """
        if not (0 <= row < self.height and 0 <= col < self.width):
            return None
        value = grid[row * self.width + col]
        if value < 3:
            return None
        return self.room_id_by_grid.get(value)

    def as_dict(self) -> dict[str, Any]:
        """The facts about the map that a card needs to know."""
        return {
            "width": self.width,
            "height": self.height,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "resolution": self.resolution,
            "scale": self.scale,
            "image_width": self.image_width,
            "image_height": self.image_height,
        }
