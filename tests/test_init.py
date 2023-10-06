"""The tests for the patch integration."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from homeassistant.const import CONF_DELAY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.setup import async_setup_component


async def async_setup(
    hass: HomeAssistant, config: ConfigType | None = None, delay: int = 0
) -> None:
    """Load patch custom integration."""
    config = config or {}
    config[CONF_DELAY] = delay
    assert await async_setup_component(
        hass,
        "patch",
        {"patch": config},
    )
    await hass.async_block_till_done()


async def test_no_config(hass: HomeAssistant) -> None:
    """Test empty configuration."""
    await async_setup(hass)


@patch("custom_components.patch.asyncio.sleep")
async def test_delay(
    sleep_mock: AsyncMock,
    hass: HomeAssistant,
) -> None:
    """Test delay configuration."""
    await async_setup(hass, None, 123)
    wait_times = [x.args[0] for x in sleep_mock.await_args_list]
    assert wait_times.count(123) == 1
