"""Update local files."""

from __future__ import annotations

import asyncio
import datetime
import posixpath
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypedDict

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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

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
        vol.Optional(CONF_NAME): cv.string,
        vol.Required(CONF_DESTINATION): vol.All(
            cv.string, expand_path, vol.Coerce(Path)
        ),
        vol.Required(CONF_BASE): vol.Any(
            vol.All(cv.url, vol.Coerce(URL)),
            vol.All(cv.string, expand_path, vol.Coerce(Path)),
        ),
        vol.Required(CONF_PATCH): vol.Any(
            vol.All(cv.url, vol.Coerce(URL)),
            vol.All(cv.string, expand_path, vol.Coerce(Path)),
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


class PatchType(TypedDict):
    """Type for patch parameters."""

    name: NotRequired[str]
    destination: Path
    base: Path | URL
    patch: Path | URL


def validate_patch(patch: PatchType) -> PatchType:
    """Compose full path (if needed) and validate file existence."""
    if name := patch.get(CONF_NAME):
        del patch[CONF_NAME]

    for param in (CONF_BASE, CONF_DESTINATION, CONF_PATCH):
        path = patch[param]
        if isinstance(path, Path):
            if name:
                path /= name
                patch[param] = path
            cv.isfile(str(path))
        elif name and param != CONF_DESTINATION:  # 2nd condition is for linters
            patch[param] = path.with_path(posixpath.join(path.path, name))

    return patch


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
                    cv.ensure_list, [CONFIG_FILE_SCHEMA], [validate_patch]
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
        config = await async_hass_config_yaml(hass)
        if DOMAIN not in config:
            message = f"'{DOMAIN}' section was not found in {YAML_CONFIG_FILE}"
            raise IntegrationError(message)
        await PatchManager(hass, CONFIG_SCHEMA({DOMAIN: config[DOMAIN]})[DOMAIN]).run()

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, async_reload, vol.Schema({}))

    event.async_track_point_in_time(
        hass,
        PatchManager(hass, config[DOMAIN]).run_after_migration,
        dt_util.now() + datetime.timedelta(seconds=config[DOMAIN][CONF_DELAY]),
    )

    return True


class Patch:
    """Single patch."""

    def __init__(self, hass: HomeAssistant, config: PatchType) -> None:
        """Initialize the object."""
        self._http_client = async_get_clientsession(hass)
        self.config = config

    async def _read(self, path: Path | URL) -> str:
        """Read file content."""
        if isinstance(path, Path):
            async with aiofiles.open(path) as file:
                return await file.read()
        async with self._http_client.get(path) as response:
            response.raise_for_status()
            return await response.text()

    async def init(self) -> None:
        """Get the content of the files."""
        self._destination, self._base, self._patch = await asyncio.gather(
            self._read(self.config[CONF_DESTINATION]),
            self._read(self.config[CONF_BASE]),
            self._read(self.config[CONF_PATCH]),
        )

    def _is_base(self) -> bool:
        """Check if the destination is identical to the base file."""
        return self._destination == self._base

    def _is_patched(self) -> bool:
        """Check if the destination is identical to the patch file."""
        return self._destination == self._patch

    def check(self) -> bool:
        """Check if patch is needed and then if it's as base."""
        if not self._is_patched() and not self._is_base():
            LOGGER.error(
                "Destination file '%s' is different than its base '%s'.",
                self.config[CONF_DESTINATION],
                self.config[CONF_BASE],
            )
            return False
        return True

    async def apply(self) -> bool:
        """Copy the patch file to the destination."""
        if self._is_patched():
            LOGGER.debug(
                "Destination file '%s' is identical to the patch file '%s'.",
                self.config[CONF_DESTINATION],
                self.config[CONF_PATCH],
            )
            return False

        async with aiofiles.open(self.config[CONF_DESTINATION], "w") as file:
            await file.write(self._patch)

        LOGGER.warning(
            "Destination file '%s' was updated by the patch file '%s'.",
            self.config[CONF_DESTINATION],
            self.config[CONF_PATCH],
        )

        return True


class PatchManager:
    """Patch manager for list of patches."""

    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:
        """Initialize the object."""
        self._hass = hass
        self._config = config
        self._patches = [Patch(hass, patch) for patch in config.get(CONF_FILES, [])]

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
        if not self._patches:
            return

        await asyncio.gather(*(patch.init() for patch in self._patches))

        if base_mismatch := [
            patch.config for patch in self._patches if not patch.check()
        ]:
            self._repair(base_mismatch)
            return

        results = await asyncio.gather(*(patch.apply() for patch in self._patches))
        updates = [
            patch.config for index, patch in enumerate(self._patches) if results[index]
        ]
        if updates:
            self._applied(updates)
            if self._config[CONF_RESTART]:
                LOGGER.warning("Restarting HA core.")
                await self._hass.services.async_call(
                    HA_DOMAIN, SERVICE_HOMEASSISTANT_RESTART
                )

    def _repair(self, files: list[PatchType]) -> None:
        """Report an issue of base file mismatch."""
        file_names = ", ".join(f'"{file[CONF_DESTINATION]}"' for file in files)
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
            severity=ir.IssueSeverity.ERROR,
            translation_key="base_mismatch",
            translation_placeholders={
                "files": message,
            },
        )

    def _applied(self, files: list[PatchType]) -> None:
        """Report the system was patched."""
        count = len(files)
        LOGGER.warning(f"{count} core file {'s were' if count > 1 else 'was'} patched.")

        ir.async_create_issue(
            self._hass,
            DOMAIN,
            "system_was_patched",
            is_fixable=False,
            is_persistent=True,
            learn_more_url="https://github.com/amitfin/patch#configuration",
            severity=ir.IssueSeverity.WARNING,
            translation_key="system_update",
            translation_placeholders={
                "files": ", ".join(f'"{file[CONF_DESTINATION]}"' for file in files),
            },
        )
