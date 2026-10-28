"""Flow code contracts with HA form/update boundaries stubbed, no credentials/network."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from hisense_food_manager.auth import FoodManagerConnectionError, FoodManagerAuthenticationError
from hisense_food_manager.const import CONF_CONNECTLIFE_USERNAME, CONF_CONNECTLIFE_PASSWORD, CONF_GROCY_URL, CONF_GROCY_API_KEY


class FlowBase:
    def __init_subclass__(cls, **kwargs): pass
    def async_show_form(self, **kwargs): return kwargs


class FlowRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ha = types.ModuleType('homeassistant')
        ha.config_entries = SimpleNamespace(ConfigFlow=FlowBase, OptionsFlow=FlowBase)
        aio = types.ModuleType('homeassistant.helpers.aiohttp_client')
        aio.async_get_clientsession = lambda hass, **kwargs: None
        self.modules = patch.dict(sys.modules, {'homeassistant': ha, 'homeassistant.helpers.aiohttp_client': aio})
        self.modules.start()
        path = Path(__file__).parents[1] / 'custom_components/hisense_food_manager/config_flow.py'
        spec = importlib.util.spec_from_file_location('hisense_food_manager._tested_flow', path)
        self.flow_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.flow_module)

    def tearDown(self): self.modules.stop()

    async def test_reauth_network_error_retains_form_and_does_not_update(self):
        flow = self.flow_module.HisenseFoodManagerConfigFlow()
        entry = SimpleNamespace(data={CONF_CONNECTLIFE_USERNAME: 'fixed-account'})
        flow._get_reauth_entry = lambda: entry
        flow.async_update_reload_and_abort = Mock()
        auth = SimpleNamespace(async_login=AsyncMock(side_effect=FoodManagerConnectionError('offline')))
        with patch.object(self.flow_module, 'FoodManagerAuthSession', return_value=auth):
            self.assertEqual((await flow.async_step_reauth({}))['step_id'], 'reauth_confirm')
            result = await flow.async_step_reauth_confirm({CONF_CONNECTLIFE_PASSWORD: 'synthetic'})
        self.assertEqual(result['errors'], {'base': 'cannot_connect'})
        flow.async_update_reload_and_abort.assert_not_called()

    async def test_reauth_updates_existing_entry_and_keeps_account(self):
        flow = self.flow_module.HisenseFoodManagerConfigFlow()
        entry = SimpleNamespace(data={CONF_CONNECTLIFE_USERNAME: 'fixed-account'})
        flow._get_reauth_entry = lambda: entry
        flow.async_update_reload_and_abort = Mock(return_value={'reason': 'reauth_successful'})
        auth = SimpleNamespace(async_login=AsyncMock())
        with patch.object(self.flow_module, 'FoodManagerAuthSession', return_value=auth) as factory:
            result = await flow.async_step_reauth_confirm({CONF_CONNECTLIFE_PASSWORD: 'synthetic'})
        factory.assert_called_once_with('fixed-account', 'synthetic')
        flow.async_update_reload_and_abort.assert_called_once_with(entry, data_updates={CONF_CONNECTLIFE_PASSWORD: 'synthetic'})
        self.assertEqual(result['reason'], 'reauth_successful')

    async def test_user_flow_classifies_auth_and_transport_errors(self):
        data = {CONF_GROCY_URL: 'http://never.invalid', CONF_GROCY_API_KEY: 'synthetic',
                CONF_CONNECTLIFE_USERNAME: 'fixed', CONF_CONNECTLIFE_PASSWORD: 'synthetic'}
        for error, expected in ((FoodManagerConnectionError(), 'cannot_connect'), (FoodManagerAuthenticationError(), 'invalid_auth')):
            flow = self.flow_module.HisenseFoodManagerConfigFlow()
            flow.hass = object()
            with patch.object(self.flow_module, 'GrocyClient', return_value=SimpleNamespace(async_system_info=AsyncMock())), \
                 patch.object(self.flow_module, 'FoodManagerAuthSession', return_value=SimpleNamespace(async_login=AsyncMock(side_effect=error))):
                result = await flow.async_step_user(data)
            self.assertEqual(result['errors']['base'], expected)

    async def test_configure_failed_entry_reloads_after_valid_login(self):
        entry = SimpleNamespace(entry_id='test', data={CONF_CONNECTLIFE_USERNAME: 'fixed'})
        flow = self.flow_module.HisenseFoodManagerOptionsFlow(entry)
        entries = SimpleNamespace(async_update_entry=Mock(), async_reload=AsyncMock())
        flow.hass = SimpleNamespace(data={}, config_entries=entries)
        flow.async_create_entry = Mock(return_value={'type': 'create_entry'})
        with patch.object(self.flow_module, 'FoodManagerAuthSession', return_value=SimpleNamespace(async_login=AsyncMock())):
            await flow.async_step_init({CONF_CONNECTLIFE_USERNAME: 'fixed', CONF_CONNECTLIFE_PASSWORD: 'synthetic'})
        entries.async_reload.assert_awaited_once_with('test')
