"""Constants for the patch integration."""

import logging
from typing import Final

DOMAIN: Final = "patch"
LOGGER = logging.getLogger(__package__)

CONF_DESTINATION: Final = "destination"
CONF_FILES = "files"
CONF_PATCH = "patch"
CONF_RESTART = "restart"
DEFAULT_DELAY_SECONDS = 300

SERVICE_HOMEASSISTANT_RESTART: Final = "restart"
