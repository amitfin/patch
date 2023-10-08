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
from homeassistant.helpers import event
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
        for file in (base, destination, patch):
            if not await aiofiles.os.path.isfile(file):
                raise FileNotFoundError(f"{file} doesn't exist")
        async with aiofiles.open(base) as file:
            base_content = await file.read()
        async with aiofiles.open(destination) as file:
            destination_content = await file.read()
        async with aiofiles.open(patch) as file:
            patch_content = await file.read()
        if destination_content != base_content:
            LOGGER.warning(
                "Destination file '%s' is different than it's base '%s'.",
                destination,
                base,
            )
            return False
        if destination_content == patch_content:
            LOGGER.debug(
                "Destination file '%s' is identical to the patch file '%s'.",
                destination,
                patch,
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
