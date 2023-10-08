"""The tests for the patch integration."""
from __future__ import annotations

import datetime
import os
import tempfile
from unittest.mock import AsyncMock, patch

import voluptuous as vol

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.const import (
    CONF_DELAY,
    CONF_NAME,
    CONF_SOURCE,
    SERVICE_HOMEASSISTANT_RESTART,
    SERVICE_RELOAD,
)
import homeassistant.core as ha
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers.typing import ConfigType
from homeassistant.setup import async_setup_component
import homeassistant.util.dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.patch.const import (
    CONF_DESTINATION,
    CONF_FILES,
    DEFAULT_DELAY_SECONDS,
    DOMAIN,
)


async def async_setup(hass: HomeAssistant, config: ConfigType | None = None) -> None:
    """Load patch custom integration."""
    assert await async_setup_component(
        hass,
        "patch",
        {"patch": config or {}},
    )
    await hass.async_block_till_done()


async def async_next_day(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Jump to the next day and execute all pending timers."""
    freezer.move_to(dt_util.now() + datetime.timedelta(days=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_empty_config(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Test empty configuration."""
    await async_setup(hass)
    await async_next_day(hass, freezer)


@pytest.mark.parametrize(
    ["delay", "expected_delay"],
    [(None, DEFAULT_DELAY_SECONDS), (123, 123), (0, 0)],
    ids=["default", "custom", "zero"],
)
@patch("homeassistant.helpers.event.async_track_point_in_time")
async def test_delay(
    async_track_point_in_time_mock: AsyncMock,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    delay: int | None,
    expected_delay: int,
) -> None:
    """Test empty configuration."""
    now = datetime.datetime.fromisoformat("2000-01-01")
    freezer.move_to(now)
    await async_setup(hass, {CONF_DELAY: delay} if delay is not None else None)
    await async_next_day(hass, freezer)
    assert async_track_point_in_time_mock.call_count == 1
    assert (
        async_track_point_in_time_mock.call_args[0][2].timestamp()
        == (now + datetime.timedelta(seconds=expected_delay)).timestamp()
    )


@patch("homeassistant.core.ServiceRegistry.async_call")
@pytest.mark.parametrize(
    ["source_content", "destination_content", "restart"],
    [("old", "new", True), ("abc", "abc", False)],
    ids=["update", "identical"],
)
async def test_patch(
    async_call_mock: AsyncMock,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    source_content: str,
    destination_content: str,
    restart: bool,
) -> None:
    """Test updating a file."""
    with tempfile.TemporaryDirectory() as source:
        with tempfile.TemporaryDirectory() as destination:
            with open(os.path.join(source, "file"), "w", encoding="ascii") as file:
                file.write(source_content)
            with open(os.path.join(destination, "file"), "w", encoding="ascii") as file:
                file.write(destination_content)
            await async_setup(
                hass,
                {
                    CONF_FILES: [
                        {
                            CONF_NAME: "file",
                            CONF_SOURCE: source,
                            CONF_DESTINATION: destination,
                        }
                    ]
                },
            )
            await async_next_day(hass, freezer)
            with open(os.path.join(destination, "file"), encoding="ascii") as file:
                assert file.read() == source_content
    assert async_call_mock.call_count == (1 if restart else 0)
    if restart:
        assert async_call_mock.await_args_list[0].args[0] == ha.DOMAIN
        assert (
            async_call_mock.await_args_list[0].args[1] == SERVICE_HOMEASSISTANT_RESTART
        )


async def test_reload(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test reload service."""
    await async_setup(hass)
    await async_next_day(hass, freezer)
    core_reload_calls = []

    @callback
    async def async_core_reload_mock(service_call: ServiceCall) -> None:
        """Mock for core reload."""
        core_reload_calls.append(service_call)

    hass.services.async_register(
        ha.DOMAIN,
        SERVICE_HOMEASSISTANT_RESTART,
        async_core_reload_mock,
        vol.Schema({}),
    )

    with tempfile.TemporaryDirectory() as source:
        with tempfile.TemporaryDirectory() as destination:
            with open(os.path.join(source, "file"), "w", encoding="ascii") as file:
                file.write("123")
            with open(os.path.join(destination, "file"), "w", encoding="ascii") as file:
                file.write("456")
            with patch(
                "homeassistant.config.load_yaml_config_file",
                return_value={
                    DOMAIN: {
                        CONF_FILES: [
                            {
                                CONF_NAME: "file",
                                CONF_SOURCE: source,
                                CONF_DESTINATION: destination,
                            }
                        ]
                    }
                },
            ):
                await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)
                await hass.async_block_till_done()
            with open(os.path.join(destination, "file"), encoding="ascii") as file:
                assert file.read() == "123"
    assert len(core_reload_calls) == 1


async def test_reload_no_config(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test reload service with no configuration."""
    await async_setup(hass)
    await async_next_day(hass, freezer)
    with patch(
        "homeassistant.config.load_yaml_config_file",
        return_value={},
    ):
        with pytest.raises(IntegrationError):
            await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)


async def test_no_file(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test file doesn't exist."""
    await async_setup(hass)
    await async_next_day(hass, freezer)
    with patch(
        "homeassistant.config.load_yaml_config_file",
        return_value={
            DOMAIN: {
                CONF_FILES: [
                    {
                        CONF_NAME: "file",
                        CONF_SOURCE: "dummy_source",
                        CONF_DESTINATION: "dummy_destination",
                    }
                ]
            }
        },
    ):
        with pytest.raises(FileNotFoundError):
            await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)
