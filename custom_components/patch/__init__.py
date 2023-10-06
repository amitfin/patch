"""Update local files."""
from __future__ import annotations

import asyncio

from homeassistant.const import CONF_DELAY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DEFAULT_DELAY_SECONDS, DOMAIN


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up domain."""
    await Patch(hass, config[DOMAIN]).run()
    return True


class Patch:
    """Patch local files."""

    def __init__(self, hass: HomeAssistant, config: ConfigType) -> None:
        """Initialize the object."""
        self._hass = hass
        self._config = config

    async def run(self) -> bool:
        """Execute."""
        await asyncio.sleep(self._config.get(CONF_DELAY, DEFAULT_DELAY_SECONDS))
