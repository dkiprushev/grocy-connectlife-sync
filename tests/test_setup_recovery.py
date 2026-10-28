"""Actual entry functions with a minimal HA boundary, not a live HA scheduler."""
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
from types import SimpleNamespace

import hisense_food_manager as integration
from hisense_food_manager.auth import FoodManagerAuthSession, FoodManagerConnectionError, FoodManagerAuthenticationError, create_connectlife_api
from hisense_food_manager._vendor.connectlife.api import LifeConnectAuthError, LifeConnectError
from hisense_food_manager.const import DOMAIN, CONF_GROCY_URL, CONF_GROCY_API_KEY, CONF_CONNECTLIFE_USERNAME, CONF_CONNECTLIFE_PASSWORD


class NotReady(Exception): pass
class AuthFailed(Exception): pass


class Services:
    def __init__(self): self.handlers = {}
    def has_service(self, domain, name): return name in self.handlers
    def async_register(self, domain, name, handler, **kwargs): self.handlers[name] = handler
    def async_remove(self, domain, name): self.handlers.pop(name, None)


class SetupRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_gigya_http_200_provider_error_is_not_invalid_credentials(self):
        for code, expected in ((500001, LifeConnectError), (429001, LifeConnectError), (403006, LifeConnectAuthError)):
            with self.subTest(code=code):
                api = create_connectlife_api('synthetic', 'synthetic')
                api._json = AsyncMock(return_value={'errorCode': code})
                context = MagicMock()
                context.__aenter__.return_value = SimpleNamespace(status=200)
                session = SimpleNamespace(post=Mock(return_value=context))
                with self.assertRaises(expected) as raised:
                    await api._login_to_gigya(session)
                self.assertIs(type(raised.exception), expected)
                self.assertEqual(raised.exception.status, code // 1000)

    async def test_transport_and_provider_outage_are_retryable(self):
        for err in (TimeoutError(), OSError(), LifeConnectError('schema'),
                    LifeConnectAuthError('busy', status=503), LifeConnectAuthError('rate', status=429), ValueError('json')):
            with self.subTest(error=type(err).__name__):
                api = SimpleNamespace(login=AsyncMock(side_effect=err), _fetch_access_token=AsyncMock(side_effect=err))
                with patch('hisense_food_manager.auth.create_connectlife_api', return_value=api):
                    auth = FoodManagerAuthSession('synthetic', 'synthetic')
                    with self.assertRaises(FoodManagerConnectionError): await auth.async_login()
                    with self.assertRaises(FoodManagerConnectionError): await auth.async_api()

    async def test_rejected_credentials_are_auth_failure(self):
        api = SimpleNamespace(login=AsyncMock(side_effect=LifeConnectAuthError('denied', status=401)))
        with patch('hisense_food_manager.auth.create_connectlife_api', return_value=api):
            with self.assertRaises(FoodManagerAuthenticationError):
                await FoodManagerAuthSession('synthetic', 'synthetic').async_login()

    async def test_setup_failure_then_retry_recovers_without_unknown_service(self):
        entry = SimpleNamespace(entry_id='test', data={CONF_GROCY_URL: 'http://never.invalid', CONF_GROCY_API_KEY: 'synthetic',
            CONF_CONNECTLIFE_USERNAME: 'synthetic', CONF_CONNECTLIFE_PASSWORD: 'synthetic'})
        hass = SimpleNamespace(data={}, services=Services(), config_entries=SimpleNamespace(async_entries=lambda domain: [entry]))
        modules = {}
        for name in ('homeassistant', 'homeassistant.helpers', 'homeassistant.helpers.aiohttp_client', 'homeassistant.helpers.storage', 'homeassistant.exceptions'):
            modules[name] = types.ModuleType(name)
        modules['homeassistant.helpers.aiohttp_client'].async_get_clientsession = lambda hass: None
        modules['homeassistant.helpers.storage'].Store = lambda *args: object()
        modules['homeassistant.exceptions'].ConfigEntryNotReady = NotReady
        modules['homeassistant.exceptions'].ConfigEntryAuthFailed = AuthFailed
        auth = SimpleNamespace(async_login=AsyncMock(side_effect=[FoodManagerConnectionError('offline'), None]))
        with patch.dict(sys.modules, modules), patch.object(integration, 'FoodManagerAuthSession', return_value=auth):
            await integration.async_setup(hass, {})
            handler = hass.services.handlers['sync_adaptive']
            with self.assertRaises(NotReady): await integration.async_setup_entry(hass, entry)
            self.assertNotIn(DOMAIN, hass.data)
            call = SimpleNamespace(data={'shopping_list_name': 'Test'})
            self.assertEqual(await handler(call), {'available': False, 'skipped': True, 'reason': 'entry_not_ready'})
            self.assertTrue(await integration.async_setup_entry(hass, entry))
            adaptive = SimpleNamespace(async_run=AsyncMock(return_value={'recovered': True}))
            hass.data[DOMAIN]['test']['adaptive_sync'] = adaptive
            self.assertEqual(await handler(call), {'recovered': True})
            adaptive.async_run.assert_awaited_once_with(1, 'Test')
            await integration.async_unload_entry(hass, entry)
            self.assertIn('sync_adaptive', hass.services.handlers)
            self.assertEqual((await handler(call))['reason'], 'entry_not_ready')

    async def test_invalid_credentials_produce_ha_auth_failed(self):
        # Exercise same entry code but stop before any runtime/services exist.
        entry = SimpleNamespace(entry_id='test', data={CONF_GROCY_URL: 'http://never.invalid', CONF_GROCY_API_KEY: 'synthetic',
            CONF_CONNECTLIFE_USERNAME: 'synthetic', CONF_CONNECTLIFE_PASSWORD: 'synthetic'})
        modules = {name: types.ModuleType(name) for name in ('homeassistant.helpers.aiohttp_client', 'homeassistant.helpers.storage', 'homeassistant.exceptions')}
        modules['homeassistant.helpers.aiohttp_client'].async_get_clientsession = lambda hass: None
        modules['homeassistant.helpers.storage'].Store = lambda *args: object()
        modules['homeassistant.exceptions'].ConfigEntryAuthFailed = AuthFailed
        auth = SimpleNamespace(async_login=AsyncMock(side_effect=FoodManagerAuthenticationError('denied')))
        with patch.dict(sys.modules, modules), patch.object(integration, 'FoodManagerAuthSession', return_value=auth):
            with self.assertRaises(AuthFailed):
                await integration.async_setup_entry(SimpleNamespace(data={}), entry)
