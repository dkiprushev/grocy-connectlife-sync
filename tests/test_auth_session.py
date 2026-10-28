import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))
from hisense_food_manager.auth import (  # noqa: E402
    FoodManagerAuthenticationError,
    FoodManagerAuthSession,
    create_connectlife_api,
)


class _FakeApi:
    def __init__(self):
        self.login_calls = 0
        self.refresh_calls = 0

    async def login(self):
        self.login_calls += 1

    async def _fetch_access_token(self):
        self.refresh_calls += 1


class _BrokenApi:
    async def login(self):
        from hisense_food_manager._vendor.connectlife.api import LifeConnectAuthError
        raise LifeConnectAuthError("bad credentials", status=401)


class AuthSessionTests(unittest.TestCase):
    def test_private_client_never_imports_other_integration_or_global_package(self):
        with patch.dict(sys.modules, {
            "custom_components.connectlife": None,
            "custom_components.connectlife.client": None,
            "connectlife": None,
            "connectlife.api": None,
        }):
            api = create_connectlife_api("user", "password")
        self.assertEqual(
            api.__class__.__module__,
            "hisense_food_manager._vendor.connectlife.api",
        )

    def test_global_client_can_coexist_with_private_client(self):
        try:
            from connectlife.api import LifeConnectTermsNotAcceptedError
        except ImportError:
            self.skipTest("The independent ConnectLife 0.13 client is not installed")
        private_client = create_connectlife_api("user", "password")
        self.assertIsNotNone(LifeConnectTermsNotAcceptedError)
        self.assertNotEqual(private_client.__class__.__module__, "connectlife.api")

    def test_session_uses_client_refresh_before_every_request(self):
        api = _FakeApi()
        with patch("hisense_food_manager.auth.create_connectlife_api", return_value=api):
            session = FoodManagerAuthSession("user", "password")
            asyncio.run(session.async_login())
            self.assertIs(asyncio.run(session.async_api()), api)
        self.assertEqual(api.login_calls, 1)
        self.assertEqual(api.refresh_calls, 1)

    def test_login_error_does_not_expose_provider_details(self):
        with patch("hisense_food_manager.auth.create_connectlife_api", return_value=_BrokenApi()):
            session = FoodManagerAuthSession("user", "password")
            with self.assertRaises(FoodManagerAuthenticationError) as raised:
                asyncio.run(session.async_login())
        self.assertEqual(str(raised.exception), "ConnectLife login failed")


if __name__ == "__main__":
    unittest.main()
