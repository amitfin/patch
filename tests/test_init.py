"""The tests for the patch integration."""

from __future__ import annotations

import datetime
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock, patch

import homeassistant.core as ha
import homeassistant.util.dt as dt_util
import pytest
import voluptuous as vol
import voluptuous.error as vol_error
import yaml
from aiohttp.client_exceptions import ClientResponseError
from homeassistant.const import (
    CONF_BASE,
    CONF_DELAY,
    CONF_NAME,
    SERVICE_RELOAD,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.patch import expand_path
from custom_components.patch.const import (
    CONF_DESTINATION,
    CONF_FILES,
    CONF_PATCH,
    CONF_RESTART,
    DEFAULT_DELAY_SECONDS,
    DOMAIN,
    SERVICE_HOMEASSISTANT_RESTART,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.helpers.typing import ConfigType
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType | None = None) -> None:
    """Load patch custom integration."""
    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: config or {}},
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def async_next_minutes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float = 1
) -> None:
    """Jump to the next minutes and execute all pending timers."""
    freezer.move_to(dt_util.now() + datetime.timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def async_next_day(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Jump to the next day and execute all pending timers."""
    await async_next_minutes(hass, freezer, 60 * 24)


async def test_empty_config(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Test empty configuration."""
    await async_setup(hass)
    await async_next_day(hass, freezer)


@pytest.mark.parametrize(
    ("delay", "expected_delay"),
    [(None, DEFAULT_DELAY_SECONDS), (123, 123), (0, 0)],
    ids=["default", "custom", "zero"],
)
@patch("homeassistant.helpers.event.async_track_point_in_time")
async def test_delay(
    async_track_point_in_time_mock: Mock,
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
    ("base_content", "destination_content", "patch_content", "update", "restart"),
    [
        ("old", "old", "new", True, True),
        ("old", "old", "new", True, False),
        ("def", "abc", "abc", False, True),
        ("abc", "def", "ghi", False, True),
    ],
    ids=["update", "update no restart", "identical", "different base"],
)
@pytest.mark.allowed_logs(
    ["Destination file", "1 core file was patched.", "Restarting HA core."]
)
async def test_patch(  # noqa: PLR0913
    async_call_mock: AsyncMock,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    base_content: str,
    destination_content: str,
    patch_content: str,
    update: bool,  # noqa: FBT001
    restart: bool,  # noqa: FBT001
) -> None:
    """Test updating a file."""
    repairs = async_capture_events(hass, ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED)
    with (
        tempfile.TemporaryDirectory() as base,
        tempfile.TemporaryDirectory() as destination,
        tempfile.TemporaryDirectory() as patch_dir,
    ):
        with (Path(base) / "file").open("w", encoding="ascii") as file:
            file.write(base_content)
        with (Path(destination) / "file").open("w", encoding="ascii") as file:
            file.write(destination_content)
        with (Path(patch_dir) / "file").open("w", encoding="ascii") as file:
            file.write(patch_content)
        await async_setup(
            hass,
            {
                CONF_RESTART: restart,
                CONF_FILES: [
                    {
                        CONF_NAME: "file",
                        CONF_BASE: base,
                        CONF_DESTINATION: destination,
                        CONF_PATCH: patch_dir,
                    }
                ],
            },
        )
        await async_next_day(hass, freezer)
        with (Path(destination) / "file").open(encoding="ascii") as file:
            assert file.read() == (
                patch_content
                if base_content == destination_content
                else destination_content
            )
    assert async_call_mock.call_count == (1 if update and restart else 0)
    assert len(repairs) == (1 if update or destination_content != patch_content else 0)
    if update:
        assert repairs[0].data["action"] == "create"
        assert repairs[0].data["domain"] == DOMAIN
        assert repairs[0].data["issue_id"] == "system_was_patched"
        if restart:
            assert async_call_mock.await_args_list[0].args[0] == ha.DOMAIN
            assert (
                async_call_mock.await_args_list[0].args[1]
                == SERVICE_HOMEASSISTANT_RESTART
            )
            assert "Restarting HA core." in caplog.text
    elif destination_content == patch_content:
        assert "is identical to the patch file" in caplog.text
    else:
        assert "is different than its base" in caplog.text
        assert repairs[0].data["action"] == "create"
        assert repairs[0].data["domain"] == DOMAIN
        assert repairs[0].data["issue_id"].startswith("patch_file_base_mismatch")


@patch("homeassistant.core.ServiceRegistry.async_call")
@pytest.mark.parametrize(
    "full_path",
    [False, True],
    ids=["name", "full path"],
)
@pytest.mark.allowed_logs(
    ["Destination file", "1 core file was patched.", "Restarting HA core."]
)
async def test_patch_url(
    async_call_mock: AsyncMock,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    full_path: bool,  # noqa: FBT001
) -> None:
    """Test updating a file using URLs."""
    repairs = async_capture_events(hass, ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED)
    aioclient_mock.get("https://test.com/base/file", text="old")
    aioclient_mock.get("https://test.com/patch/file", text="new")
    with tempfile.TemporaryDirectory() as destination:
        with (Path(destination) / "file").open("w", encoding="ascii") as file:
            file.write("old")
        patch = {
            CONF_DESTINATION: destination,
            CONF_BASE: "https://test.com/base",
            CONF_PATCH: "https://test.com/patch",
        }
        if full_path:
            for key in patch:
                patch[key] += "/file"
        else:
            patch[CONF_NAME] = "file"
        await async_setup(hass, {CONF_FILES: [patch]})
        await async_next_day(hass, freezer)
        with (Path(destination) / "file").open(encoding="ascii") as file:
            assert file.read() == "new"
    assert async_call_mock.call_count == 1
    assert repairs[0].data["issue_id"] == "system_was_patched"


@pytest.mark.allowed_logs(
    ["Destination file", "1 core file was patched.", "Restarting HA core."]
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

    with (
        tempfile.TemporaryDirectory() as base,
        tempfile.TemporaryDirectory() as destination,
        tempfile.TemporaryDirectory() as patch_dir,
    ):
        with (Path(base) / "file").open("w", encoding="ascii") as file:
            file.write("123")
        with (Path(destination) / "file").open("w", encoding="ascii") as file:
            file.write("123")
        with (Path(patch_dir) / "file").open("w", encoding="ascii") as file:
            file.write("456")
        with patch(
            "homeassistant.config.load_yaml_config_file",
            return_value={
                DOMAIN: {
                    CONF_FILES: [
                        {
                            CONF_NAME: "file",
                            CONF_BASE: base,
                            CONF_DESTINATION: destination,
                            CONF_PATCH: patch_dir,
                        }
                    ]
                }
            },
        ):
            await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)
            await hass.async_block_till_done(wait_background_tasks=True)
        with (Path(destination) / "file").open(encoding="ascii") as file:
            assert file.read() == "456"
    assert len(core_reload_calls) == 1


async def test_reload_no_config(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test reload service with no configuration."""
    await async_setup(hass)
    await async_next_day(hass, freezer)
    with (
        patch(
            "homeassistant.config.load_yaml_config_file",
            return_value={},
        ),
        pytest.raises(IntegrationError),
    ):
        await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)


async def test_invalid_config(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test file doesn't exist."""
    await async_setup(hass)
    await async_next_day(hass, freezer)
    with (
        patch(
            "homeassistant.config.load_yaml_config_file",
            return_value={
                DOMAIN: {
                    CONF_FILES: [
                        {
                            CONF_DESTINATION: "test",
                            CONF_BASE: "http://test.com",
                            CONF_PATCH: "http://test.com",
                        }
                    ]
                }
            },
        ),
        pytest.raises(vol_error.MultipleInvalid) as err,
    ):
        await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)
    assert "not a file @ data['patch']['files'][0]" in str(err.value)


async def test_url_fetch_error(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test URL download error."""
    aioclient_mock.get("https://test.com/file", status=404)
    with tempfile.TemporaryDirectory() as dest_dir:
        destination = Path(dest_dir) / "file"
        with (destination).open("w", encoding="ascii") as file:
            file.write("test")
        await async_setup(hass, {CONF_FILES: []})
        await async_next_day(hass, freezer)
        with (
            patch(
                "homeassistant.config.load_yaml_config_file",
                return_value={
                    DOMAIN: {
                        CONF_FILES: [
                            {
                                CONF_DESTINATION: destination,
                                CONF_BASE: "https://test.com/file",
                                CONF_PATCH: "https://test.com/file",
                            }
                        ]
                    }
                },
            ),
            pytest.raises(ClientResponseError) as error,
        ):
            await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)
    assert error.value.status == 404


async def test_no_delay(
    hass: HomeAssistant,
) -> None:
    """Test no delay."""
    await async_setup(hass, {CONF_DELAY: 0})


@pytest.mark.allowed_logs(
    [
        "Invalid config for 'patch'",
        "Setup failed for custom integration 'patch': Invalid config.",
    ]
)
async def test_negative_delay(
    hass: HomeAssistant,
) -> None:
    """Test negative delay."""
    assert not await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: {CONF_DELAY: -1}},
    )


def test_expand_path() -> None:
    """Test path with variables."""
    for variable in ["site-packages", "homeassistant"]:
        assert expand_path(f"{{{variable}}}").endswith(f"{os.path.sep}{variable}")


async def test_expand_path_config(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Test configuration with variables."""
    await async_setup(
        hass,
        yaml.load(
            """
            files:
              - name: __init__.py
                base: "{site-packages}/homeassistant"
                destination: "{site-packages}/homeassistant"
                patch: "{site-packages}/homeassistant"
              - name: __init__.py
                base: "{homeassistant}"
                destination: "{homeassistant}"
                patch: "{homeassistant}"
            """,
            Loader=yaml.SafeLoader,
        ),
    )
    await async_next_day(hass, freezer)


@patch("homeassistant.helpers.recorder.async_migration_in_progress")
async def test_wait_for_recorder_migration(
    async_migration_in_progress_mock: Mock,
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test waiting for recorder migration to complete."""

    def _delay_count(log: str) -> int:
        return log.count("Recorder migration in progress. Checking again in a minute.")

    await async_setup(hass)
    async_migration_in_progress_mock.return_value = True
    await async_next_minutes(hass, freezer, DEFAULT_DELAY_SECONDS / 60)
    assert _delay_count(caplog.text) == 1
    for i in range(2, 10):
        await async_next_minutes(hass, freezer)
        assert _delay_count(caplog.text) == i
    async_migration_in_progress_mock.return_value = False
    await async_next_day(hass, freezer)
    assert _delay_count(caplog.text) == i


@pytest.mark.allowed_logs(["Destination file"])
async def test_immediate_base_mismatch(hass: HomeAssistant) -> None:
    """Test base mismatch is reported during integration setup."""
    repairs = async_capture_events(hass, ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED)
    await async_setup(
        hass,
        yaml.load(
            """
            files:
              - destination: "{homeassistant}/__init__.py"
                base: "{homeassistant}/__main__.py"
                patch: "{homeassistant}/py.typed"
            """,
            Loader=yaml.SafeLoader,
        ),
    )
    assert repairs[0].data["issue_id"].startswith("patch_file_base_mismatch")
