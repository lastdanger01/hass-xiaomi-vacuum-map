"""The flow that sets up the integration."""

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_DID,
    CONF_MAP,
    COUNTRIES,
    DEFAULT_COUNTRY,
    DEFAULT_DID,
    DOMAIN,
    MAP_OBJECTS,
)

_LOGGER = logging.getLogger(__name__)


class XiaomiVacuumMapFlow(ConfigFlow, domain=DOMAIN):
    """The flow that sets up the map of the robot.

    The account of the cloud of Xiaomi is not asked for here. The
    accounts of the integration xiaomi_miot are used, and the one that
    holds the robot of this house is found when the integration is set
    up.
    """

    VERSION = 1

    async def async_step_user(self,
                              user_input: dict | None = None) -> ConfigFlowResult:
        """Ask which robot and which map should be shown."""
        if user_input is not None:
            return self.async_create_entry(
                title="Vacuum map",
                data={
                    CONF_DID: user_input.get(CONF_DID) or DEFAULT_DID,
                    CONF_MAP: user_input.get(CONF_MAP) or MAP_OBJECTS[0],
                },
            )

        schema = vol.Schema({
            vol.Optional(CONF_DID, default=DEFAULT_DID): str,
            vol.Optional(CONF_MAP, default=MAP_OBJECTS[0]): str,
        })

        return self.async_show_form(step_id="user", data_schema=schema)
