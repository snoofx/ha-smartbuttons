"""Constants of the SmartButtons integration."""

DOMAIN = "smartbuttons"

# The panel firmware listens here for the integration (ha.h, HA_LINK_PORT).
LINK_PORT = 81

EVENT_BUTTON_PRESSED = f"{DOMAIN}_button_pressed"

STORAGE_KEY = f"{DOMAIN}.panels"
STORAGE_VERSION = 1

PANEL_URL_PATH = DOMAIN
PANEL_COMPONENT = "smartbuttons-panel"
STATIC_URL = f"/{DOMAIN}_static"

# Attributes the panel shows. Everything else stays in HA, light attributes can be kilobytes.
FORWARDED_ATTRIBUTES = (
    "hvac_action",
    "current_temperature",
    "temperature",
    "min_temp",
    "max_temp",
    "rgb_color",
)

# Keys in a panel layout that hold entities the panel may control or show.
ENTITY_KEYS = ("entity_id", "state_entity", "window_entity")

# The firmware reads its layout into a 12 KiB JSON document (CONFIG_DOC_SIZE).
MAX_LAYOUT_BYTES = 8000

CONF_LINK_KEY = "link_key"

# Password for the web UI and OTA of all panels, set by HA.
PASSWORD_LENGTH = 16
PASSWORD_MAX = 32

SIGNAL_PANEL_UPDATED = f"{DOMAIN}_panel_updated_{{}}"
