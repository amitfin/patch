"""Update local files."""
from __future__ import annotations

import datetime
import os
import voluptuous as vol

import aiofiles
import aiofiles.os

from homeassistant import config as config_utils
from homeassistant.const import (
    CONF_BASE,
    CONF_DELAY,
    CONF_NAME,
    SERVICE_HOMEASSISTANT_RESTART,
    SERVICE_RELOAD,
)
import homeassistant.core as ha
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers import config_validation as cv, event
from homeassistant.helpers.typing import ConfigType
import homeassistant.util.dt as dt_util

from .const import (
    CONF_DESTINATION,
    CONF_FILES,
    CONF_PATCH,
    DEFAULT_DELAY_SECONDS,
    DOMAIN,
    LOGGER,
)

CONFIG_FILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_BASE): cv.isdir,
        vol.Required(CONF_DESTINATION): cv.isdir,
        vol.Required(CONF_PATCH): cv.isdir,
    },
    extra=vol.ALLOW_EXTRA,
)


def validate_files(single_patch: dict[str, str]) -> dict[str, str]:
    """Validate all files of a patch configuration."""
    for dir_property in (CONF_BASE, CONF_DESTINATION, CONF_PATCH):
        cv.isfile(os.path.join(single_patch[dir_property], single_patch[CONF_NAME]))
    return single_patch


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_DELAY): vol.All(
                    vol.Coerce(int), vol.Range(min=0, min_included=True)
                ),
                vol.Optional(CONF_FILES): vol.All(
                    cv.ensure_list, [CONFIG_FILE_SCHEMA], [validate_files]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up domain."""

    async def async_reload(_: ServiceCall) -> None:
        """Patch the core files using the new configuration."""
        config = config_utils.load_yaml_config_file(
            hass.config.path(config_utils.YAML_CONFIG_FILE)
        )
        if DOMAIN not in config:
            raise IntegrationError(
                f"'{DOMAIN}' section was not found in {config_utils.YAML_CONFIG_FILE}"
            )
        CONFIG_SCHEMA({DOMAIN: config[DOMAIN]})
        await Patch(hass, config[DOMAIN]).run()

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, async_reload, vol.Schema({}))

    event.async_track_point_in_time(
        hass,
        Patch(hass, config[DOMAIN]).run,
        dt_util.now()
        + datetime.timedelta(
            seconds=config[DOMAIN].get(CONF_DELAY, DEFAULT_DELAY_SECONDS)
        ),
    )

    return True


class Patch:
    """Patch local files."""

    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:
        """Initialize the object."""
        self._hass = hass
        self._config = config

    @callback
    async def run(self, *_) -> None:
        """Execute."""
        update = False
        for file in self._config.get(CONF_FILES, []):
            if await self._patch(
                file[CONF_NAME],
                file[CONF_BASE],
                file[CONF_DESTINATION],
                file[CONF_PATCH],
            ):
                update = True
        if update:
            LOGGER.warning("Core file(s) were patched. Restarting HA core.")
            await self._hass.services.async_call(
                ha.DOMAIN, SERVICE_HOMEASSISTANT_RESTART
            )

    async def _patch(
        self,
        name: str,
        base_directory: str,
        destination_directory: str,
        patch_directory: str,
    ) -> bool:
        """Check if identical files and update the destination if needed."""
        base = os.path.join(base_directory, name)
        destination = os.path.join(destination_directory, name)
        patch = os.path.join(patch_directory, name)
        async with aiofiles.open(base) as file:
            base_content = await file.read()
        async with aiofiles.open(destination) as file:
            destination_content = await file.read()
        async with aiofiles.open(patch) as file:
            patch_content = await file.read()
        if destination_content == patch_content:
            LOGGER.debug(
                "Destination file '%s' is identical to the patch file '%s'.",
                destination,
                patch,
            )
            return False
        if destination_content != base_content:
            LOGGER.error(
                "Destination file '%s' is different than it's base '%s'.",
                destination,
                base,
            )
            return False
        async with aiofiles.open(destination, "w") as file:
            await file.write(patch_content)
        LOGGER.warning(
            "Destination file '%s' was updated by the patch file '%s'.",
            destination,
            patch,
        )
        return True
