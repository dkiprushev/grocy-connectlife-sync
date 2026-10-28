"""Config flow. Credentials are entered in the HA UI and never written to YAML."""
from __future__ import annotations
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .auth import FoodManagerAuthSession, FoodManagerAuthenticationError, FoodManagerConnectionError
from .const import (
    CONF_CONNECTLIFE_PASSWORD,
    CONF_CONNECTLIFE_USERNAME,
    CONF_GROCY_API_KEY,
    CONF_GROCY_URL,
    DEFAULT_GROCY_URL,
    DOMAIN,
)
from .grocy import GrocyClient, GrocyError

class HisenseFoodManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_reauth(self, entry_data):
        """Repair rejected credentials without creating another integration."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            # Keep the account fixed so existing stock baselines cannot be
            # silently reused for a different household during reauth.
            auth = FoodManagerAuthSession(entry.data[CONF_CONNECTLIFE_USERNAME], user_input[CONF_CONNECTLIFE_PASSWORD])
            try:
                await auth.async_login()
            except FoodManagerConnectionError:
                errors["base"] = "cannot_connect"
            except FoodManagerAuthenticationError:
                errors["base"] = "invalid_auth"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CONNECTLIFE_PASSWORD): str}),
            errors=errors,
        )

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                client = GrocyClient(async_get_clientsession(self.hass, family=GrocyClient.session_family(user_input[CONF_GROCY_URL])), user_input[CONF_GROCY_URL], user_input[CONF_GROCY_API_KEY])
                await client.async_system_info()
                auth = FoodManagerAuthSession(
                    user_input[CONF_CONNECTLIFE_USERNAME],
                    user_input[CONF_CONNECTLIFE_PASSWORD],
                )
                await auth.async_login()
            except (GrocyError, OSError, FoodManagerConnectionError):
                errors["base"] = "cannot_connect"
            except FoodManagerAuthenticationError:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(user_input[CONF_GROCY_URL].rstrip("/"))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Grocy ConnectLife Sync", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_GROCY_URL, default=DEFAULT_GROCY_URL): str,
                vol.Required(CONF_GROCY_API_KEY): str,
                vol.Required(CONF_CONNECTLIFE_USERNAME): str,
                vol.Required(CONF_CONNECTLIFE_PASSWORD): str,
            }),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return HisenseFoodManagerOptionsFlow(config_entry)


class HisenseFoodManagerOptionsFlow(config_entries.OptionsFlow):
    """Allow an existing entry to add or replace Food Manager credentials."""

    def __init__(self, config_entry=None) -> None:
        # ``config_entry`` is a framework-owned property in recent Home
        # Assistant versions.  Assigning to it raises during flow creation
        # and produces the UI's otherwise opaque HTTP 500.  Retain the entry
        # privately for releases that still pass it to the constructor.
        self._initial_config_entry = config_entry

    @property
    def _entry(self):
        """Return the entry supplied by either HA flow API generation."""
        entry = getattr(self, "config_entry", None)
        if entry is not None:
            return entry
        if self._initial_config_entry is not None:
            return self._initial_config_entry
        raise RuntimeError("Home Assistant did not provide the config entry to the options flow")

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                auth = FoodManagerAuthSession(
                    user_input[CONF_CONNECTLIFE_USERNAME],
                    user_input[CONF_CONNECTLIFE_PASSWORD],
                )
                await auth.async_login()
            except FoodManagerConnectionError:
                errors["base"] = "cannot_connect"
            except FoodManagerAuthenticationError:
                errors["base"] = "invalid_auth"
            else:
                updated_data = {
                    **self._entry.data,
                    **user_input,
                }
                self.hass.config_entries.async_update_entry(
                    self._entry, data=updated_data
                )
                # The options flow has already authenticated this exact
                # account.  Reuse that in-memory client immediately; on the
                # next HA start setup_entry creates an equivalent new session
                # from the saved credentials.
                runtime = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
                if isinstance(runtime, dict):
                    runtime["auth"] = auth
                else:
                    # A failed setup has no runtime to replace. Updating saved
                    # credentials alone would otherwise leave it failed.
                    await self.hass.config_entries.async_reload(self._entry.entry_id)
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_CONNECTLIFE_USERNAME): str,
                vol.Required(CONF_CONNECTLIFE_PASSWORD): str,
            }),
            errors=errors,
        )
