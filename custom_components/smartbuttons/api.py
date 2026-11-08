"""Editor panel in the sidebar and its WebSocket commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, PANEL_COMPONENT, PANEL_URL_PATH, PASSWORD_MAX, STATIC_URL
from .link import PanelLink, hostname
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
        embed_iframe=False,
        require_admin=True,
    )


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    for command in (ws_panels, ws_configs, ws_get_config, ws_save_config, ws_create_config,
                    ws_delete_config, ws_update_panel, ws_locate, ws_get_password, ws_set_password):
        websocket_api.async_register_command(hass, command)


def _links(hass: HomeAssistant) -> list[PanelLink]:
    return [entry.runtime_data for entry in hass.config_entries.async_loaded_entries(DOMAIN)]


def _link(hass: HomeAssistant, entry_id: str) -> PanelLink | None:
    return next((link for link in _links(hass) if link.entry.entry_id == entry_id), None)


def _store(hass: HomeAssistant) -> LayoutStore:
    return hass.data[DOMAIN]


def _push_config(hass: HomeAssistant, config_id: str) -> dict[str, int]:
    """Rolls a configuration out to every panel that uses it. Offline panels get it later."""
    store = _store(hass)
    users = [link for link in _links(hass) if store.panels.get(link.panel_id, {}).get("config") == config_id]
    pushed = sum(link.push_layout() for link in users)
    return {"pushed": pushed, "offline": len(users) - pushed}


@websocket_api.websocket_command({vol.Required("type"): "smartbuttons/panels"})
@websocket_api.require_admin
@callback
def ws_panels(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    store = _store(hass)
    panels = []
    for link in _links(hass):
        panel = store.panels.get(link.panel_id, {})
        panels.append({**link.status(), "config": panel.get("config"), "rotate_180": bool(panel.get("rotate_180"))})
    panels.sort(key=lambda p: p["name"].lower())
    connection.send_result(msg["id"], panels)


@websocket_api.websocket_command({vol.Required("type"): "smartbuttons/configs"})
@websocket_api.require_admin
@callback
def ws_configs(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    store = _store(hass)
    names = {link.panel_id: link.name for link in _links(hass)}
    configs = [
        {"id": cid, "name": c["name"], "panels": [names.get(pid, pid) for pid in store.users(cid)]}
        for cid, c in store.configs.items()
    ]
    configs.sort(key=lambda c: c["name"].lower())
    connection.send_result(msg["id"], configs)


@websocket_api.websocket_command(
    {vol.Required("type"): "smartbuttons/get_config", vol.Required("config_id"): str}
)
@websocket_api.require_admin
@callback
def ws_get_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    config = _store(hass).configs.get(msg["config_id"])
    if config is None:
        connection.send_error(msg["id"], "not_found", "Configuration not found")
        return
    connection.send_result(msg["id"], config["layout"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartbuttons/save_config",
        vol.Required("config_id"): str,
        vol.Optional("name"): vol.All(str, vol.Length(min=1, max=40)),
        vol.Optional("config"): dict,
    }
)
@websocket_api.require_admin
@callback
def ws_save_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    store = _store(hass)
    if msg["config_id"] not in store.configs:
        connection.send_error(msg["id"], "not_found", "Configuration not found")
        return
    try:
        layout = clean_layout(msg["config"]) if "config" in msg else None
    except LayoutError as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    store.update_config(msg["config_id"], name=msg.get("name"), layout=layout)
    result = _push_config(hass, msg["config_id"]) if layout is not None else {"pushed": 0, "offline": 0}
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartbuttons/create_config",
        vol.Required("name"): vol.All(str, vol.Length(min=1, max=40)),
        vol.Optional("copy_from"): str,
    }
)
@websocket_api.require_admin
@callback
def ws_create_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    store = _store(hass)
    source = store.configs.get(msg.get("copy_from", ""))
    config_id = store.create_config(msg["name"], source["layout"] if source else None)
    connection.send_result(msg["id"], {"config_id": config_id})


@websocket_api.websocket_command(
    {vol.Required("type"): "smartbuttons/delete_config", vol.Required("config_id"): str}
)
@websocket_api.require_admin
@callback
def ws_delete_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    try:
        _store(hass).delete_config(msg["config_id"])
    except (LayoutError, KeyError) as err:
        connection.send_error(msg["id"], "in_use", str(err))
        return
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartbuttons/update_panel",
        vol.Required("entry_id"): str,
        vol.Optional("name"): vol.All(str, vol.Length(min=1, max=31)),
        vol.Optional("config_id"): str,
        vol.Optional("rotate_180"): bool,
    }
)
@websocket_api.require_admin
@callback
def ws_update_panel(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Rename (the panel takes it as hostname), assign a configuration, set the mounting."""
    link = _link(hass, msg["entry_id"])
    store = _store(hass)
    if link is None or not store.has_panel(link.panel_id):
        connection.send_error(msg["id"], "not_found", "Panel not found or not connected yet")
        return
    try:
        store.set_panel(link.panel_id, config=msg.get("config_id"), rotate_180=msg.get("rotate_180"))
    except LayoutError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    if "config_id" in msg or "rotate_180" in msg:
        link.push_layout()
    if "name" in msg:
        name = hostname(msg["name"])
        # The update listener of the entry sends the name to the panel.
        hass.config_entries.async_update_entry(link.entry, title=name)
    connection.send_result(msg["id"], {"name": link.entry.title})


@websocket_api.websocket_command({vol.Required("type"): "smartbuttons/locate", vol.Required("entry_id"): str})
@websocket_api.require_admin
@callback
def ws_locate(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Ping pong in the top row of the panel, its name and address below."""
    link = _link(hass, msg["entry_id"])
    if link is None or not link.connected:
        connection.send_error(msg["id"], "not_connected", "Panel is not connected")
        return
    if not link.locate():
        connection.send_error(msg["id"], "not_supported", "Panel firmware too old, update it (0.9.2 or newer)")
        return
    connection.send_result(msg["id"])


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
