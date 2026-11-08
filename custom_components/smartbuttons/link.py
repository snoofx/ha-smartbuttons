"""Connection from Home Assistant to one panel.

HA connects to the panel (ws://<panel>:81/). The panel says hello with its id, layout revision
and the entities it shows. HA answers with the layout if the panel's one is outdated, otherwise
with "welcome" and the states, and from then on relays state changes and service calls. The
messages are the same as the HA WebSocket API, so the firmware handles both transports alike.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import hmac
import json
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, __version__ as HA_VERSION
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.json import json_dumps
from homeassistant.util import slugify

from .const import (
    CONF_LINK_KEY,
    DOMAIN,
    EVENT_BUTTON_PRESSED,
    FEATURE_PROTOCOL,
    FORWARDED_ATTRIBUTES,
    LINK_PORT,
    PROTOCOL,
    SIGNAL_PANEL_UPDATED,
)
from .store import EMPTY_LAYOUT, LayoutError, LayoutStore, clean_layout, layout_entities

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
ADOPT_TIMEOUT = 40
# A restarting panel is back after a few seconds: try often then, and give up a hanging
# connect quickly. Long pauses only when it stays away.
LINK_CONNECT_TIMEOUT = 4
RETRY_FAST = 1
FAST_PHASE = 120
RETRY_MIN = 2
RETRY_MAX = 60
HEARTBEAT = 10      # a panel that lost power is noticed within about 15 s
MAX_MESSAGE = 64 * 1024


def hostname(name: str) -> str:
    """Panel name as the panel uses it: hostname, a-z 0-9 and "-", at most 31 characters."""
    return slugify(name, separator="-")[:31].strip("-") or "smartbuttons"


def state_payload(state: State | None) -> dict[str, Any]:
    """A state in the compressed form of subscribe_entities, only what the panel shows."""
    if state is None:
        return {"s": "unavailable", "a": {}}
    attrs = {k: state.attributes[k] for k in FORWARDED_ATTRIBUTES if k in state.attributes}
    return {"s": state.state, "a": attrs}


async def async_read_hello(hass: HomeAssistant, host: str) -> dict[str, Any]:
    """Connects once and returns the panel's hello. Raises on failure."""
    session = async_get_clientsession(hass)
    async with asyncio.timeout(CONNECT_TIMEOUT):
        async with session.ws_connect(f"ws://{host}:{LINK_PORT}/", max_msg_size=MAX_MESSAGE) as ws:
            msg = await ws.receive()
            hello = json.loads(msg.data) if msg.type == aiohttp.WSMsgType.TEXT else {}
            if hello.get("type") != "hello" or not hello.get("id"):
                raise ValueError("no SmartButtons panel")
            return hello


def link_proof(key: str, nonce: str) -> str:
    """Answer to the nonce of a hello: HMAC-SHA256 with the pairing key."""
    return hmac.new(bytes.fromhex(key), nonce.encode(), hashlib.sha256).hexdigest()


async def _async_prove(ws: aiohttp.ClientWebSocketResponse, key: str) -> bool:
    """Reads the hello and proves the key. The panel only answers a wrong proof (auth_failed)."""
    msg = await ws.receive()
    hello = json.loads(msg.data) if msg.type == aiohttp.WSMsgType.TEXT else {}
    if not hello.get("paired"):
        return False
    await ws.send_str(json_dumps({"type": "auth", "hmac": link_proof(key, str(hello.get("nonce", "")))}))
    try:
        reply = await ws.receive(timeout=2)
    except TimeoutError:
        return True
    if reply.type != aiohttp.WSMsgType.TEXT:
        return False  # closed after a wrong proof
    return json.loads(reply.data).get("type") != "auth_failed"


async def async_verify_key(hass: HomeAssistant, host: str, key: str) -> bool:
    """True if the panel is paired with this key."""
    session = async_get_clientsession(hass)
    async with asyncio.timeout(CONNECT_TIMEOUT):
        async with session.ws_connect(f"ws://{host}:{LINK_PORT}/", max_msg_size=MAX_MESSAGE) as ws:
            return await _async_prove(ws, key)


async def async_adopt(hass: HomeAssistant, host: str, key: str, password: str) -> None:
    """Switches a locally managed, unpaired panel to Home Assistant and pairs it. It restarts.
    A panel with password only accepts it together with that password. Success is checked on
    the restarted panel, not by the reply: that can get lost when the panel restarts."""
    session = async_get_clientsession(hass)
    async with asyncio.timeout(CONNECT_TIMEOUT):
        async with session.ws_connect(f"ws://{host}:{LINK_PORT}/", max_msg_size=MAX_MESSAGE) as ws:
            await ws.receive()  # hello
            await ws.send_str(json_dumps({"type": "adopt", "key": key, "password": password}))
            try:
                reply = await ws.receive(timeout=5)
                if reply.type == aiohttp.WSMsgType.TEXT and json.loads(reply.data).get("type") == "config_error":
                    raise ValueError("panel refused the takeover (password?)")
            except TimeoutError:
                pass
    async with asyncio.timeout(ADOPT_TIMEOUT):
        while True:
            await asyncio.sleep(2)
            try:
                hello = await async_read_hello(hass, host)
                if hello.get("mode") == "ha" and await async_verify_key(hass, host, key):
                    return
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
                pass  # restarting


async def async_unpair(hass: HomeAssistant, host: str, key: str) -> None:
    """Lets the panel go, so another HA (or this one again) can add it."""
    session = async_get_clientsession(hass)
    async with asyncio.timeout(CONNECT_TIMEOUT):
        async with session.ws_connect(f"ws://{host}:{LINK_PORT}/", max_msg_size=MAX_MESSAGE) as ws:
            if await _async_prove(ws, key):
                await ws.send_str(json_dumps({"type": "unpair"}))
                await ws.receive(timeout=3)


class PanelLink:
    """Keeps the connection to one panel and relays between it and HA."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: LayoutStore) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self.panel_id: str = entry.unique_id or ""
        self.host: str = entry.data[CONF_HOST]
        self._key: str = entry.data[CONF_LINK_KEY]
        self.auth_failed = False  # the panel is paired with another HA
        self.hellos = 0  # counts restarts and reconnects, for the firmware update
        self._name_requested: str | None = None
        self._wake = asyncio.Event()
        self.name: str = entry.title
        self.mode: str | None = None  # "ha" or "local", from the last hello
        self.version: str | None = None
        self.proto = 1  # message version of the panel, from the hello
        self.connected = False  # welcomed: states and calls flow
        self.device_id: str | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._unsub_states: Any = None
        self._allowed: set[str] = set()
        self._warned: set[str] = set()

    # ── Status ──

    def status(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry.entry_id,
            "id": self.panel_id,
            "name": self.name,
            "host": self.host,
            "mode": self.mode,
            "version": self.version,
            "proto": self.proto,
            "connected": self.connected,
            "auth_failed": self.auth_failed,
        }

    @callback
    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            async_dispatcher_send(self.hass, SIGNAL_PANEL_UPDATED.format(self.entry.entry_id))

    # ── Connection ──

    @callback
    def start(self) -> None:
        self.entry.async_create_background_task(self.hass, self._run(), f"{DOMAIN} {self.host}")

    async def _run(self) -> None:
        session = async_get_clientsession(self.hass)
        delay = RETRY_MIN
        fast_until = time.monotonic() + FAST_PHASE
        while True:
            try:
                async with asyncio.timeout(LINK_CONNECT_TIMEOUT):
                    ws = await session.ws_connect(
                        f"ws://{self.host}:{LINK_PORT}/", heartbeat=HEARTBEAT, max_msg_size=MAX_MESSAGE
                    )
                delay = RETRY_MIN
                _LOGGER.debug("%s: connected", self.host)
                try:
                    await self._session(ws)
                finally:
                    await ws.close()
                    fast_until = time.monotonic() + FAST_PHASE  # it restarts, probably
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                _LOGGER.debug("%s: connection failed: %s", self.host, err)
            finally:
                self._end_session()
            if time.monotonic() < fast_until:
                wait = min(RETRY_FAST, delay)
            else:
                wait, delay = delay, min(delay * 2, RETRY_MAX)
            # mDNS announcements of the panel cut the pause short, see wake().
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
            except TimeoutError:
                pass
            self._wake.clear()

    @callback
    def wake(self) -> None:
        """The panel announced itself (started): reconnect now instead of after the pause."""
        if not self.connected:
            self._wake.set()

    async def _session(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._queue = asyncio.Queue()
        sender = self.entry.async_create_background_task(
            self.hass, self._sender(ws, self._queue), f"{DOMAIN} {self.host} sender"
        )
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    _LOGGER.warning("%s: invalid message", self.host)
                    continue
                await self._handle(data)
        finally:
            sender.cancel()

    @staticmethod
    async def _sender(ws: aiohttp.ClientWebSocketResponse, queue: asyncio.Queue[str]) -> None:
        # One writer keeps the order of state messages.
        while True:
            await ws.send_str(await queue.get())

    @callback
    def _send(self, message: dict[str, Any]) -> None:
        if self._ws is not None:
            self._queue.put_nowait(json_dumps(message))

    @callback
    def _end_session(self) -> None:
        self._ws = None
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        self._set_connected(False)

    # ── Messages from the panel ──

    async def _handle(self, data: dict[str, Any]) -> None:
        kind = data.get("type")
        if kind == "hello":
            self._on_hello(data)
        elif kind == "call_service":
            self.entry.async_create_task(self.hass, self._on_call(data))
        elif kind == "press":
            self._on_press(data)
        elif kind == "config_error":
            _LOGGER.error("%s rejected its layout: %s", self.name, data.get("message"))
        elif kind == "config_saved":
            _LOGGER.info("%s took the new layout and restarts", self.name)
        elif kind == "auth_failed":
            if not self.auth_failed:
                _LOGGER.error(
                    "%s is paired with another Home Assistant. Reset it on the panel (debug screen: long "
                    "press SW10 on START, then SW6), then it pairs with this one",
                    self.name,
                )
            self.auth_failed = True
            async_dispatcher_send(self.hass, SIGNAL_PANEL_UPDATED.format(self.entry.entry_id))
        elif kind == "paired":
            _LOGGER.info("%s paired with this Home Assistant", self.name)

    @callback
    def _on_hello(self, hello: dict[str, Any]) -> None:
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        self._set_connected(False)
        self.mode = hello.get("mode")
        self.version = hello.get("version")
        proto = hello.get("proto", 1)
        self.proto = proto if isinstance(proto, int) and proto >= 1 else 1
        self.hellos += 1
        self.update_device()
        async_dispatcher_send(self.hass, SIGNAL_PANEL_UPDATED.format(self.entry.entry_id))

        if not hello.get("paired"):
            # Fresh or reset panel: it takes the key, then says hello again.
            self._send({"type": "pair", "key": self._key, "password": self.store.password})
            return
        self._send({"type": "auth", "hmac": link_proof(self._key, str(hello.get("nonce", "")))})
        self.auth_failed = False

        if self.mode != "ha":
            _LOGGER.info("%s is managed locally, switch it in its web UI", self.name)
            async_dispatcher_send(self.hass, SIGNAL_PANEL_UPDATED.format(self.entry.entry_id))
            return

        if self._rename(str(hello.get("name", ""))):
            return  # the panel restarts with the new hostname

        if not self.store.has_panel(self.panel_id):
            # First contact: the panel's layout becomes its own configuration, nothing gets lost.
            panel_layout = hello.get("config") or {}
            try:
                layout = clean_layout(panel_layout if panel_layout.get("buttons") is not None else EMPTY_LAYOUT)
            except LayoutError as err:
                _LOGGER.warning("%s: layout of the panel not taken over: %s", self.name, err)
                layout = EMPTY_LAYOUT
            self.store.adopt_panel(self.panel_id, self.entry.title, layout, bool(panel_layout.get("rotate_180")))
        layout = self.store.layout_for(self.panel_id)
        rev = self.store.revision(self.panel_id)
        if hello.get("rev") != rev:
            self._send({"type": "config", "rev": rev, "config": layout})
            return  # the panel restarts, or says hello again

        self._allowed = layout_entities(layout)
        watch = [e for e in hello.get("watch", []) if isinstance(e, str)]
        self._send({"type": "password", "password": self.store.password})
        self._send({"type": "welcome", "ha_version": HA_VERSION, "proto": PROTOCOL})
        states = {}
        for entity_id in watch:
            state = self.hass.states.get(entity_id)
            if state is None and entity_id not in self._warned:
                self._warned.add(entity_id)
                _LOGGER.warning("%s shows %s, which does not exist in HA", self.name, entity_id)
            states[entity_id] = state_payload(state)
        if states:
            self._send({"type": "event", "event": {"a": states}})
            self._unsub_states = async_track_state_change_event(self.hass, watch, self._on_state)
        self._set_connected(True)

    @callback
    def _rename(self, panel_name: str) -> bool:
        """Sends the entry title as hostname if the panel has another. Asks only once per name:
        a panel that keeps its name (invalid, or the old default "smartbuttons") is followed."""
        target = hostname(self.entry.title)
        if not panel_name or target == panel_name:
            return False
        if self.entry.title != "smartbuttons" and self._name_requested != target:
            self._name_requested = target
            self._send({"type": "name", "name": target})
            return True
        self.name = panel_name
        self.hass.config_entries.async_update_entry(self.entry, title=panel_name)
        self.update_device()
        return False

    @callback
    def update_device(self) -> None:
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, self.panel_id)},
            connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(self.panel_id))},
            manufacturer="SmartButtons",
            model="SA6432 Panel",
            name=self.entry.title,
            sw_version=self.version,
            configuration_url=f"http://{self.host}/",
        )
        self.device_id = device.id

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        self._send({"type": "event", "event": {"a": {entity_id: state_payload(event.data["new_state"])}}})

    async def _on_call(self, data: dict[str, Any]) -> None:
        domain = data.get("domain")
        service = data.get("service")
        target = (data.get("target") or {}).get("entity_id")
        service_data = data.get("service_data") or {}
        ok = False
        # A panel may only control what its layout names, within the entity's own domain.
        if (
            isinstance(target, str)
            and target in self._allowed
            and target.split(".", 1)[0] == domain
            and isinstance(service, str)
            and isinstance(service_data, dict)
        ):
            try:
                await self.hass.services.async_call(
                    domain,
                    service,
                    {**service_data, ATTR_ENTITY_ID: target},
                    blocking=True,
                    context=Context(),
                )
                ok = True
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("%s: %s.%s on %s failed: %s", self.name, domain, service, target, err)
        else:
            _LOGGER.warning("%s: refused %s.%s on %s, not in its layout", self.name, domain, service, target)
        self._send({"type": "result", "id": data.get("id"), "success": ok})

    @callback
    def _on_press(self, data: dict[str, Any]) -> None:
        self.hass.bus.async_fire(
            EVENT_BUTTON_PRESSED,
            {
                "device_id": self.device_id,
                "panel": self.name,
                "button": data.get("button"),
                "gesture": data.get("gesture"),
                "page": data.get("page"),
            },
        )

    # ── From the editor ──

    @callback
    def push_password(self) -> None:
        if self.connected:
            self._send({"type": "password", "password": self.store.password})

    @callback
    def push_layout(self) -> bool:
        """Sends the stored layout if the panel listens. It restarts when the layout changed."""
        if self._ws is None or self.mode != "ha":
            return False
        self._send(
            {"type": "config", "rev": self.store.revision(self.panel_id), "config": self.store.layout_for(self.panel_id)}
        )
        return True

    def supports(self, feature: str) -> bool:
        """The panel's firmware knows this message (FEATURE_PROTOCOL)."""
        return self.proto >= FEATURE_PROTOCOL[feature]

    @callback
    def locate(self) -> bool:
        """The panel shows where it hangs until a key is pressed (at most 5 minutes)."""
        if not (self.connected and self.supports("locate")):
            return False
        self._send({"type": "locate"})
        return True

    @callback
    def wake_screen(self) -> bool:
        """Screensaver off, and the panel stays on for its screensaver time from now."""
        if not (self.connected and self.supports("wake")):
            return False
        self._send({"type": "wake"})
        return True

    @callback
    def push_name(self) -> None:
        """The entry title is the panel's name; the panel takes it as hostname and restarts."""
        self.name = self.entry.title
        self.update_device()
        if self.connected:
            self._send({"type": "name", "name": hostname(self.entry.title)})
