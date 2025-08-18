"""Update local files."""

from __future__ import annotations

import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
import homeassistant
import homeassistant.util.dt as dt_util
import voluptuous as vol
from homeassistant.config import YAML_CONFIG_FILE, async_hass_config_yaml
from homeassistant.const import (
    CONF_BASE,
    CONF_DELAY,
    CONF_NAME,
    SERVICE_RELOAD,
)
from homeassistant.core import DOMAIN as HA_DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import event, recorder
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_DESTINATION,
    CONF_FILES,
    CONF_PATCH,
    CONF_RESTART,
    DEFAULT_DELAY_SECONDS,
    DOMAIN,
    LOGGER,
    SERVICE_HOMEASSISTANT_RESTART,
)

if TYPE_CHECKING:
    from homeassistant.helpers.typing import ConfigType

PATH_VARIABLES = {
    "site-packages": str(Path(aiofiles.__file__).parent.parent),
    "homeassistant": str(Path(homeassistant.__file__).parent),
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
        cv.isfile(Path(single_patch[dir_property]) / single_patch[CONF_NAME])
    return single_patch


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_DELAY, default=DEFAULT_DELAY_SECONDS): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=0, min_included=True),
                ),
                vol.Required(CONF_RESTART, default=True): cv.boolean,
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
        config = await async_hass_config_yaml(hass)
        if DOMAIN not in config:
            message = f"'{DOMAIN}' section was not found in {YAML_CONFIG_FILE}"
            raise IntegrationError(message)
        await Patch(hass, CONFIG_SCHEMA({DOMAIN: config[DOMAIN]})[DOMAIN]).run()

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, async_reload, vol.Schema({}))

    event.async_track_point_in_time(
        hass,
        Patch(hass, config[DOMAIN]).run_after_migration,
        dt_util.now() + datetime.timedelta(seconds=config[DOMAIN][CONF_DELAY]),
    )

    return True


class Patch:
    """Patch local files."""

    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:
        """Initialize the object."""
        self._hass = hass
        self._config = config

    @callback
    async def run_after_migration(self, _: datetime.datetime | None = None) -> None:
        """Run if there is no migration in progress."""
        if recorder.async_migration_in_progress(self._hass):
            LOGGER.info("Recorder migration in progress. Checking again in a minute.")
            event.async_track_point_in_time(
                self._hass,
                self.run_after_migration,
                dt_util.now() + datetime.timedelta(minutes=1),
            )
        else:
            await self.run()

    async def run(self) -> None:
        """Execute."""
        updates = 0
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
                    updates += 1
                case PatchResult.BASE_MISMATCH:
                    base_mismatch.append(file)
        if base_mismatch:
            self._repair(base_mismatch)
        if updates > 0:
            LOGGER.warning(
                f"{updates} core file {'s were' if updates > 1 else 'was'} patched."
            )
            if self._config[CONF_RESTART]:
                LOGGER.warning("Restarting HA core.")
                await self._hass.services.async_call(
                    HA_DOMAIN, SERVICE_HOMEASSISTANT_RESTART
                )

    async def _patch(
        self,
        name: str,
        base_directory: str,
        destination_directory: str,
        patch_directory: str,
    ) -> PatchResult:
        """Check if identical files and update the destination if needed."""
        base = Path(base_directory) / name
        destination = Path(destination_directory) / name
        patch = Path(patch_directory) / name
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
        file_names = ", ".join(f'"{file[CONF_NAME]}"' for file in files)
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
