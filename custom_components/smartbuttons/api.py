"""Editor panel in the sidebar and its WebSocket commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, PANEL_COMPONENT, PANEL_URL_PATH, PASSWORD_MAX, STATIC_URL
from .link import PanelLink
from .store import LayoutError, LayoutStore, clean_layout

FRONTEND_DIR = Path(__file__).parent / "frontend"
PANEL_FILE = "smartbuttons-panel.js"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Adds "SmartButtons" to the sidebar, admins only."""
    path = FRONTEND_DIR / PANEL_FILE
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
    )
    # The modification time in the URL makes browsers load a new editor after an update.
    mtime = int((await hass.async_add_executor_job(path.stat)).st_mtime)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="SmartButtons",
        sidebar_icon="mdi:gesture-tap-button",
        module_url=f"{STATIC_URL}/{PANEL_FILE}?v={mtime}",
        embed_iframe=True,
        require_admin=True,
    )


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_panels)
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_set_config)
    websocket_api.async_register_command(hass, ws_get_password)
    websocket_api.async_register_command(hass, ws_set_password)


def _links(hass: HomeAssistant) -> list[PanelLink]:
    return [entry.runtime_data for entry in hass.config_entries.async_loaded_entries(DOMAIN)]


def _link(hass: HomeAssistant, entry_id: str) -> PanelLink | None:
    return next((link for link in _links(hass) if link.entry.entry_id == entry_id), None)


def _store(hass: HomeAssistant) -> LayoutStore:
    return hass.data[DOMAIN]


@websocket_api.websocket_command({vol.Required("type"): "smartbuttons/panels"})
@websocket_api.require_admin
@callback
def ws_panels(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    panels = sorted((link.status() for link in _links(hass)), key=lambda p: p["name"].lower())
    connection.send_result(msg["id"], panels)


@websocket_api.websocket_command(
    {vol.Required("type"): "smartbuttons/get_config", vol.Required("entry_id"): str}
)
@websocket_api.require_admin
@callback
def ws_get_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    link = _link(hass, msg["entry_id"])
    if link is None:
        connection.send_error(msg["id"], "not_found", "Panel not found")
        return
    layout = _store(hass).get(link.panel_id)
    connection.send_result(msg["id"], layout or {"buttons": [], "presets": {"entity_id": "", "scenes": []}})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartbuttons/set_config",
        vol.Required("entry_id"): str,
        vol.Required("config"): dict,
    }
)
@websocket_api.require_admin
@callback
def ws_set_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    link = _link(hass, msg["entry_id"])
    if link is None:
        connection.send_error(msg["id"], "not_found", "Panel not found")
        return
    try:
        layout = clean_layout(msg["config"])
    except LayoutError as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    rev = _store(hass).set(link.panel_id, layout)
    connection.send_result(msg["id"], {"rev": rev, "pushed": link.push_layout()})


@websocket_api.websocket_command({vol.Required("type"): "smartbuttons/get_password"})
@websocket_api.require_admin
@callback
def ws_get_password(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    connection.send_result(msg["id"], {"password": _store(hass).password})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartbuttons/set_password",
        # Printable ASCII: it goes into HTTP Basic auth and espota.
        vol.Required("password"): vol.All(str, vol.Match(r"^[\x21-\x7e]{8,%d}$" % PASSWORD_MAX)),
    }
)
@websocket_api.require_admin
@callback
def ws_set_password(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """One password for all panels. Connected panels take it at once, the others when they connect."""
    _store(hass).set_password(msg["password"])
    for link in _links(hass):
        link.push_password()
    connection.send_result(msg["id"], {"panels": sum(link.connected for link in _links(hass))})
