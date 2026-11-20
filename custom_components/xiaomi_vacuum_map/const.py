"""The settings of the integration."""

DOMAIN = "xiaomi_vacuum_map"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_COUNTRY = "country"
CONF_DID = "did"
CONF_MAP = "map"

# The endpoint that has the map of the newer models of the robot.
MAP_ENDPOINT = "/v2/home/get_interim_file_url_pro"

# How often the map is fetched from the cloud, in seconds.
DEFAULT_SCAN_INTERVAL = 300

# The names of the maps of the robot. The first one is the map of the
# last cleaning, the ones after it are the maps that are named.
MAP_OBJECTS = (
    "1793992516/1037044969/0",
    "1793992516/1037044969/1",
    "1793992516/1037044969/3",
    "1793992516/1037044969/6",
)

# The did of the robot of this house.
DEFAULT_DID = "1037044969"

# The country of the cloud of the account.
DEFAULT_COUNTRY = "cn"

COUNTRIES = ["cn", "de", "sg", "us", "tw"]
