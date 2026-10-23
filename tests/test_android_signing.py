"""Regression coverage for the Android Food Manager request format."""
from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import patch

from hisense_food_manager.connectlife import ConnectLifeFoodClient


class AndroidSigningTests(unittest.TestCase):
    def test_signer_uses_sorted_compact_android_payload(self) -> None:
        payload = {"purchases": [{"name": "Яблоки", "checked": False}], "z": 2}
        with patch("hisense_food_manager.connectlife.uuid.uuid1", return_value="uuid-v1"), patch(
            "hisense_food_manager.connectlife.time.time", return_value=1.234
        ):
            request = ConnectLifeFoodClient._android_signed_request("token", payload)

        self.assertNotIn("appSecret", request)
        self.assertEqual(request["version"], "8.1")
        self.assertEqual(request["randStr"], hashlib.md5(b"uuidv1").hexdigest())
        self.assertEqual(request["timeStamp"], 1234)
        canonical = "&".join(
            f"{key}={ConnectLifeFoodClient._android_value(request[key])}"
            for key in sorted(key for key in request if key != "sign")
        )
        expected = hashlib.sha1(
            f"{ConnectLifeFoodClient._FOOD_MANAGER_APP_SECRET}{canonical}"
            "D9519A4B756946F081B7BB5B5E8D1197".encode()
        ).hexdigest()
        self.assertEqual(request["sign"], expected)

    def test_authorize_uid_request_without_token_is_supported(self) -> None:
        with patch("hisense_food_manager.connectlife.uuid.uuid1", return_value="uuid-v1"), patch(
            "hisense_food_manager.connectlife.time.time", return_value=2.0
        ):
            request = ConnectLifeFoodClient._android_signed_request(None, {"uid": "account-uid"})

        self.assertNotIn("accessToken", request)
        self.assertEqual(request["uid"], "account-uid")
        self.assertTrue(request["sign"])

    def test_android_authorization_chains_anonymous_cdc_and_authorize_uid(self) -> None:
        class Response:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Session:
            def __init__(self):
                self.request_json = None
                self.requests = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def post(self, _url, *, data, headers):
                # The interceptor sends a JSON string containing HENC data,
                # not an object with a ``data`` property.
                self.request_json = json.loads(data)
                self.requests.append(json.loads(self.request_json))
                return Response()

        class Api:
            def __init__(self):
                self.session = Session()
                self._access_token = None

            def _client_session(self):
                return self.session

            async def _login_to_gigya(self, _session):
                return "account-uid", "gigya-cookie"

            async def _fetch_jwt(self, _session, cookie):
                if cookie != "gigya-cookie":
                    raise AssertionError("unexpected Gigya session")
                return "gigya-jwt"

            async def _json(self, _response, *, endpoint):
                return {"response": {"resultCode": 0, "resultData": "food-token"}}

        async def verify() -> None:
            api = Api()
            client = ConnectLifeFoodClient(None)
            with patch.object(
                client,
                "_android_henc_body",
                side_effect=lambda request: {"data": json.dumps(request)},
            ):
                token = await client._async_food_manager_access_token(
                    api, "https://clife-eu-gateway.hijuconn.com"
                )
            self.assertEqual(token, "food-token")
            self.assertEqual(len(api.session.requests), 3)
            self.assertEqual(api.session.requests[0]["loginName"], "")
            self.assertNotIn("accessToken", api.session.requests[0])
            self.assertEqual(api.session.requests[1]["idToken"], "gigya-jwt")
            self.assertEqual(api.session.requests[2]["uid"], "account-uid")
            self.assertEqual(api.session.requests[2]["accessToken"], "food-token")

        import asyncio

        asyncio.run(verify())

    def test_henc_body_uses_the_android_prefix(self) -> None:
        body = ConnectLifeFoodClient._android_henc_body({"hello": "world"})
        self.assertEqual(set(body), {"data"})
        self.assertTrue(body["data"].startswith("HENC-"))

    def test_eu_authorize_uid_uses_auth_gateway(self) -> None:
        self.assertEqual(
            ConnectLifeFoodClient._food_manager_authorize_base_url(
                "https://clife-eu-gateway.hijuconn.com"
            ),
            "https://auth-gateway.hijuconn.com",
        )

    def test_ru_authorize_uid_keeps_ru_gateway(self) -> None:
        self.assertEqual(
            ConnectLifeFoodClient._food_manager_authorize_base_url(
                "https://clife-ru2-gateway.hijuconn.com"
            ),
            "https://clife-ru2-gateway.hijuconn.com",
        )
