"""Update local files."""
from __future__ import annotations

import datetime
from enum import StrEnum
import os

import voluptuous as vol

import aiofiles
import aiofiles.os

import homeassistant
from homeassistant.components.homeassistant import SERVICE_HOMEASSISTANT_RESTART
from homeassistant.const import (
    CONF_BASE,
    CONF_DELAY,
    CONF_NAME,
    SERVICE_RELOAD,
)
import homeassistant.core as ha
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers import config_validation as cv, event, issue_registry as ir
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

PATH_VARIABLES = {
    "site-packages": os.path.sep.join(aiofiles.__file__.split(os.path.sep)[0:-2]),
    "homeassistant": os.path.dirname(homeassistant.__file__),
}


def expand_path(path: str) -> str:
    """Expand variables in path string."""
    return path.format(**PATH_VARIABLES)


CONFIG_FILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_BASE): vol.All(cv.string, expand_path, cv.isdir),
        vol.Required(CONF_DESTINATION): vol.All(cv.string, expand_path, cv.isdir),
        vol.Required(CONF_PATCH): vol.All(cv.string, expand_path, cv.isdir),
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


class PatchResult(StrEnum):
    """Patch result types."""

    UPDATED = "updated"
    IDENTICAL = "identical"
    BASE_MISMATCH = "base_mismatch"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up domain."""

    async def async_reload(_: ServiceCall) -> None:
        """Patch the core files using the new configuration."""
        config = homeassistant.config.load_yaml_config_file(
            hass.config.path(homeassistant.config.YAML_CONFIG_FILE)
        )
        if DOMAIN not in config:
            raise IntegrationError(
                f"'{DOMAIN}' section was not found in {homeassistant.config.YAML_CONFIG_FILE}"
            )
        await Patch(hass, CONFIG_SCHEMA({DOMAIN: config[DOMAIN]})[DOMAIN]).run()

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
        base_mismatch = []
        for file in self._config.get(CONF_FILES, []):
            result = await self._patch(
                file[CONF_NAME],
                file[CONF_BASE],
                file[CONF_DESTINATION],
                file[CONF_PATCH],
            )
            match result:
                case PatchResult.UPDATED:
                    update = True
                case PatchResult.BASE_MISMATCH:
                    base_mismatch.append(file)
        if base_mismatch:
            self._repair(base_mismatch)
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
    ) -> PatchResult:
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
            return PatchResult.IDENTICAL
        if destination_content != base_content:
            LOGGER.error(
                "Destination file '%s' is different than its base '%s'.",
                destination,
                base,
            )
            return PatchResult.BASE_MISMATCH
        async with aiofiles.open(destination, "w") as file:
            await file.write(patch_content)
        LOGGER.warning(
            "Destination file '%s' was updated by the patch file '%s'.",
            destination,
            patch,
        )
        return PatchResult.UPDATED

    def _repair(self, files: list[dict[str, str]]) -> None:
        """Report an issue of base file mismatch."""
        file_names = ", ".join(f'"{ file[CONF_NAME] }"' for file in files)
        message = (
            f"The file {file_names} is"
            if len(files) == 1
            else f"The files {file_names} are"
        )
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            "patch_file_base_mismatch_" + str(int(dt_util.now().timestamp())),
            is_fixable=False,
            learn_more_url="https://github.com/amitfin/patch#configuration",
            severity=ir.IssueSeverity.WARNING,
            translation_key="base_mismatch",
            translation_placeholders={
                "files": message,
            },
        )
