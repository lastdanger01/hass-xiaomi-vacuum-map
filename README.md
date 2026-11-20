# Xiaomi Vacuum Map

A Home Assistant custom integration that shows the map of a Xiaomi (Mijia)
robot vacuum in the cloud, as a camera entity.

The map is read from the cloud of Xiaomi and drawn as a picture, with the
rooms in their colours and the robot and the dock marked on it. The robot
itself is **never** started, stopped or sent any command: only the map and
the place of the robot are read.

## How it works

The map of this model is not a binary map. The cloud keeps it as a **json**
file, in a bucket of its own:

```
/v2/home/get_interim_file_url_pro   ->  a signed url
https://cnbj1.fds.api.xiaomi.com/xiaomi-b108za/<uid>/<did>/<map>
```

The file holds:

| field | meaning |
|---|---|
| `map_data` | base64 of a zlib packed grid, one byte for every cell |
| `width`, `height` | the size of the grid in cells |
| `origin_x`, `origin_y` | the place of the first cell, in millimetres |
| `resolution` | the size of a cell, in millimetres (50) |
| `pile_x`, `pile_y`, `pile_yaw` | the place and the direction of the **dock** |
| `map_room_info` | the rooms, with their colour |
| `room_attrs`, `fb_regions` | the settings of the rooms and the no-go areas |

The values of the cells are `0` free, `1` wall, `2` something in the way and
`3` and up the number of the room.

The place of the robot is asked for separately, with
`miotspec/prop/get` on service `7`, property `4`.

## The account

The accounts of the integration `xiaomi_miot` are used, so no name and no
word have to be given twice. The token that is in the storage of Home
Assistant is often too old to be used, so the account is signed in as well,
the same way `xiaomi_miot` does it. There can be more than one account, so
every one of them is tried until the map is really there.

## The picture

Home Assistant gives a camera ten seconds for a picture, and reading the map
from the cloud takes longer. So the picture is fetched in the background and
kept, and the call for a picture gives the picture that is there right away.

## Files

```
custom_components/xiaomi_vacuum_map/
├── __init__.py        the setup, the search for the account
├── camera.py          the camera entity
├── cloud.py           the talk with the cloud of Xiaomi
├── config_flow.py     the setup in the user interface
├── const.py           the settings
├── map_renderer.py    the drawing of the map
└── manifest.json
```

## Install

Copy `custom_components/xiaomi_vacuum_map` into the folder
`custom_components` of Home Assistant, restart Home Assistant, then add the
integration from the user interface. Nothing else has to be filled in.
