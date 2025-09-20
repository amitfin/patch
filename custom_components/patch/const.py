"""Constants for the patch integration."""

import logging
from typing import Final

DOMAIN: Final = "patch"
LOGGER = logging.getLogger(__package__)

CONF_DESTINATION: Final = "destination"
CONF_FILES: Final = "files"
CONF_PATCH: Final = "patch"
CONF_RESTART: Final = "restart"
DEFAULT_DELAY_SECONDS: Final = 300

SERVICE_HOMEASSISTANT_RESTART: Final = "restart"
