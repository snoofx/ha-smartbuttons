"""Adding panels: found by mDNS, or by address."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import CONF_LINK_KEY, DOMAIN

CONF_PASSWORD = "password"
from .link import async_adopt, async_read_hello, async_verify_key

_LOGGER = logging.getLogger(__name__)


class SmartButtonsConfigFlow(ConfigFlow, domain=DOMAIN):
    """One config entry per panel, identified by its MAC."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._name: str = ""
        self._mode: str = "ha"
        self._paired = False
        self._protected = False  # local panel with password: needed for the takeover
        # One key per flow: a retry after a lost reply uses the key the panel may already have.
        self._key = secrets.token_hex(32)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        panel_id = discovery_info.properties.get("id")
        if not panel_id:
            return self.async_abort(reason="not_smartbuttons")
        self._host = discovery_info.host
        self._name = discovery_info.properties.get("name") or discovery_info.hostname.split(".")[0]
        self._mode = discovery_info.properties.get("mode", "ha")
        self._paired = discovery_info.properties.get("paired") == "1"
        await self.async_set_unique_id(panel_id)
        # A known panel that announces itself has (re)started: let its connection retry now.
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, panel_id)
        if entry is not None and entry.state is ConfigEntryState.LOADED:
            entry.runtime_data.wake()
        # A new IP of a known panel is taken over, the entry reloads.
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        self.context["title_placeholders"] = {"name": self._name}
        if self._mode == "local":
            try:
                self._protected = bool((await async_read_hello(self.hass, self._host)).get("protected"))
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
                return self.async_abort(reason="cannot_connect")
        return await self.async_step_confirm()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                hello = await async_read_hello(self.hass, host)
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                self._host = host
                self._name = hello.get("name") or host
                self._mode = hello.get("mode", "ha")
                self._paired = bool(hello.get("paired"))
                self._protected = bool(hello.get("protected"))
                await self.async_set_unique_id(hello["id"])
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return await self.async_step_confirm()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is None and self._paired:
            # Belongs to another HA, or to this one before the panel was removed here.
            return self.async_abort(reason="paired_elsewhere")
        if user_input is not None:
            try:
                # Fresh state: the form may be older than a restart of the panel.
                hello = await async_read_hello(self.hass, self._host)
                if hello.get("paired"):
                    if not await async_verify_key(self.hass, self._host, self._key):
                        return self.async_abort(reason="paired_elsewhere")
                elif hello.get("mode") == "local":
                    await async_adopt(self.hass, self._host, self._key, user_input.get(CONF_PASSWORD, ""))
                # Managed and unpaired: the connection pairs it with the key of the entry.
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
                _LOGGER.warning("Taking over %s failed: %s", self._host, err)
                errors["base"] = "adopt_failed"
            else:
                return self.async_create_entry(
                    title=self._name, data={CONF_HOST: self._host, CONF_LINK_KEY: self._key}
                )
        local = self._mode == "local"
        schema = vol.Schema({vol.Required(CONF_PASSWORD): str} if local and self._protected else {})
        return self.async_show_form(
            step_id="confirm_local" if local else "confirm",
            data_schema=schema,
            description_placeholders={"name": self._name, "host": self._host},
            errors=errors,
        )

    async def async_step_confirm_local(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_confirm(user_input)
