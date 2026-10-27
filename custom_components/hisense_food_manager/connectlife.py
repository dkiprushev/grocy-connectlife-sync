"""Authenticated Food Manager client built on the installed ConnectLife gateway."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import base64
import hashlib
import json
import time
import uuid

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .models import FoodZone, InventoryItem, ShoppingItem, normalized_name
from .auth import FoodManagerAuthSession, FoodManagerAuthenticationError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .const import (
    CONF_CONNECTLIFE_PASSWORD,
    CONF_CONNECTLIFE_USERNAME,
    DOMAIN,
)


class ApiContractUnavailable(RuntimeError):
    """The refrigerator's Food Manager contract could not be verified safely."""


class ConnectLifeFoodClient:
    """Read Food Manager data through the signed ConnectLife gateway.

    Write endpoints deliberately remain absent until their request contracts are
    observed and validated against a labelled temporary record.
    """

    _SHOPPING_LIST_PATH = "fridge/getShoppingList"
    _INVENTORY_PATH = "fridge/pu/get_fridge_food"
    _FOOD_CATALOG_PATH = "fridge/get_all_food"
    _UNITS_PATH = "fridge/get_food_unit_with_county"
    _ADD_SHOPPING_PATH = "fridge/addShoppingList"
    _UPDATE_SHOPPING_PATH = "fridge/updateShoppingList"
    _RESET_SHOPPING_PATH = "fridge/resetShoppingList"
    _DELETE_SHOPPING_PATH = "fridge/delShoppingList"
    _ADD_INVENTORY_PATH = "fridge/pu/add_fridge_food"
    _UPDATE_INVENTORY_PATH = "fridge/pu/update_fridge_food"
    _DELETE_INVENTORY_PATH = "fridge/pu/delete_fridge_food"
    _AUTHORIZE_UID_PATH = "account/acc/authorize_uid"
    _ANDROID_ANONYMOUS_LOGIN_PATH = "account/acc/login_pwd"
    _ANDROID_TOKEN_SIGN_IN_PATH = "account/acc/token_sign_in"
    # ConnectLife Android 26.6.2 selects these EU credentials for Food
    # Manager.  They differ from the generic appliance client's identity;
    # reads work with either, while the gateway rejects certain write actions
    # with the generic identity as a signature failure.
    _FOOD_MANAGER_APP_ID = "47110563037231"
    _FOOD_MANAGER_APP_SECRET = "yfjLvJutkNLNS7x0aV6nRLBBM7iRJ-IA6dy6U_J6wGR4t50DtDBLXQsDS2tKqSVt"
    _FOOD_MANAGER_API_VERSION = "8.1"
    _CDC_USER_KEY = "AKdhV1WM7CbP"
    _CDC_USER_SECRET = "oEenVnD+ZPSZyYpN2lmZFBsRGV0+PxCH"
    _ANDROID_SOURCE_ID_PREFIX = "td0010010000"
    _android_source_id: str | None = None
    _HENC_KEY = b"aaaabbbbccccddddeeeeffffgggghhhh"
    _HENC_IV = b"aaaabbbbccccdddd"
    _SIGN_SUFFIX = "D9519A4B756946F081B7BB5B5E8D1197"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _entry(self) -> ConfigEntry:
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ApiContractUnavailable("Grocy ConnectLife Sync is not configured")
        entry = entries[0]
        if not entry.data.get(CONF_CONNECTLIFE_USERNAME) or not entry.data.get(CONF_CONNECTLIFE_PASSWORD):
            raise ApiContractUnavailable(
                "Food Manager ConnectLife credentials are unavailable; open Configure in this integration"
            )
        return entry

    async def _async_api_and_refrigerator(self) -> tuple[Any, str, Any]:
        from ._vendor.connectlife.api import GATEWAY_BASE_URL
        entry = self._entry()
        runtime = self._hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        auth = runtime.get("auth") if isinstance(runtime, dict) else None
        if not isinstance(auth, FoodManagerAuthSession):
            raise ApiContractUnavailable("Food Manager authentication session is not available")
        try:
            api = await auth.async_api()
        except FoodManagerAuthenticationError as err:
            raise ApiContractUnavailable(str(err)) from err
        refrigerator = next(
            (item for item in await api.get_appliances()
             if "refrigerator" in str(getattr(item, "device_type", "")).lower()),
            None,
        )
        if refrigerator is None:
            refrigerator = next(
                (item for item in await api.get_appliances()
                 if "refriger" in str(getattr(item, "device_nickname", "")).lower()),
                None,
            )
        if refrigerator is None:
            raise ApiContractUnavailable("No refrigerator found in ConnectLife")
        if not (getattr(refrigerator, "puid", None) or getattr(refrigerator, "device_id", None)):
            raise ApiContractUnavailable("ConnectLife refrigerator has no device identifier")
        # ``create_api`` may return the TRIR/CIS client.  Its authentication,
        # app identity and signature are different from EU, and its gateway is
        # ``clife-ru2``.  Derive the base URL from that client rather than
        # forcing every Food Manager request through the EU constant.
        update_url = getattr(api, "gateway_update_url", "")
        gateway_base_url = (
            str(update_url).removesuffix("/device/pu/property/set")
            if update_url else GATEWAY_BASE_URL
        )
        return api, gateway_base_url, refrigerator

    @staticmethod
    def _wifi_id(refrigerator: Any) -> str:
        value = getattr(refrigerator, "wifi_id", None) or getattr(refrigerator, "wifiId", None)
        if not value:
            raise ApiContractUnavailable("ConnectLife refrigerator has no Wi-Fi identifier")
        return str(value)

    @staticmethod
    def _device_id(refrigerator: Any) -> str:
        return str(getattr(refrigerator, "puid", None) or getattr(refrigerator, "device_id"))

    async def _async_get(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        api, gateway_base_url, refrigerator = await self._async_api_and_refrigerator()
        response = await api._request_gateway_json(  # noqa: SLF001 - no public Food Manager API exists
            f"{gateway_base_url}/{path}",
            payload=payload or {"puid": self._device_id(refrigerator)},
            retry_on_reauth=True,
            retry_on_randstr=True,
        )
        return response.get("resultData", response)

    async def _async_food_request(
        self, path: str, payload: dict[str, Any], *, include_device_context: bool = True
    ) -> Any:
        """Call a Food Manager endpoint with the renewable gateway OAuth token."""
        api, gateway_base_url, refrigerator = await self._async_api_and_refrigerator()
        request_payload = dict(payload)
        if include_device_context:
            request_payload = {
                "puid": self._device_id(refrigerator),
                "wifiId": self._wifi_id(refrigerator),
                **request_payload,
            }
        response = await api._request_gateway_json(  # noqa: SLF001 - vendor has no public Food API
            f"{gateway_base_url}/{path}",
            payload=request_payload,
            retry_on_reauth=True,
            retry_on_randstr=True,
        )
        return response.get("resultData", response)

    @staticmethod
    def _android_value(value: Any) -> str:
        """Serialize a value exactly as Dart's base request processor does."""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if value is True:
            return "true"
        if value is False:
            return "false"
        if value is None:
            return "null"
        return str(value)

    @classmethod
    def _android_source_identifier(cls) -> str:
        """Return the stable-per-installation source ID used by Android.

        Android generates this once with the ``td0010010000`` prefix and a
        UUID-derived MD5 suffix, then stores it in shared preferences.  A
        process-local value gives this client the same stable identity for its
        whole authentication and request session without persisting a token or
        identifier in HA storage.
        """
        if cls._android_source_id is None:
            value = str(uuid.uuid1()).replace("-", "").encode()
            cls._android_source_id = cls._ANDROID_SOURCE_ID_PREFIX + hashlib.md5(value).hexdigest()
        return cls._android_source_id

    @classmethod
    def _android_signed_request(
        cls, access_token: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Reproduce ConnectLife Android's BaseConfigProcessor signature."""
        timestamp = int(time.time() * 1000)
        nonce = f"{uuid.uuid1()}{timestamp}"
        for _ in range(6):
            nonce = hashlib.md5(nonce.encode()).hexdigest()
        request = {
            "appId": cls._FOOD_MANAGER_APP_ID,
            "languageId": "12",
            "randStr": nonce,
            "sourceId": cls._android_source_identifier(),
            "timeStamp": timestamp,
            # ``flutter_timezone`` supplies an IANA zone name on Android.
            "timezone": "Europe/Kyiv",
            "version": cls._FOOD_MANAGER_API_VERSION,
            **payload,
        }
        # ``authorize_uid`` is the Android application's bootstrap request.
        # It is signed with the Food Manager identity but, naturally, does not
        # yet contain an Android access token.
        if access_token:
            request["accessToken"] = access_token
        canonical = "&".join(
            f"{key}={cls._android_value(request[key])}" for key in sorted(request)
            if request[key] is not None and request[key] != "" and request[key] != [] and request[key] != {}
        )
        # The Android APK uses SHA-1 for the request signature. MD5 above is
        # only used to derive the compact random-string value.
        request["sign"] = hashlib.sha1(
            f"{cls._FOOD_MANAGER_APP_SECRET}{canonical}{cls._SIGN_SUFFIX}".encode()
        ).hexdigest()
        return request

    @classmethod
    def _android_henc_body(cls, request: dict[str, Any]) -> dict[str, str]:
        """Apply the Android ``ClHencInterceptor`` envelope to a JSON request."""
        raw = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(raw) + padder.finalize()
        encryptor = Cipher(algorithms.AES(cls._HENC_KEY), modes.CBC(cls._HENC_IV)).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        return {"data": "HENC-" + base64.b64encode(encrypted).decode()}

    @staticmethod
    def _food_manager_authorize_base_url(gateway_base_url: str) -> str:
        """Return the Android auth gateway paired with an appliance gateway.

        ``loginRefrigerator`` in ConnectLife Android 26.6.2 dispatches through
        ``AuthGatewayRequest``, not ``BaseGatewayRequest``. For EU accounts
        that is ``auth-gateway``; Food Manager endpoints themselves remain on
        ``clife-eu-gateway``. The RU login variant keeps its clife-ru gateway.
        """
        return gateway_base_url.replace("clife-eu-gateway.", "auth-gateway.")

    async def _async_android_post(
        self, api: Any, session: Any, url: str, request_data: dict[str, Any], *, encrypted: bool = True
    ) -> dict[str, Any]:
        """Post through Android's encrypted request interceptor."""
        async with session.post(
            url,
            data=(self._android_henc_body(request_data)["data"] if encrypted else json.dumps(request_data, ensure_ascii=False, separators=(",", ":"))),
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status != 200:
                body = await response.text()
                raise ApiContractUnavailable(
                    f"ConnectLife Android authorization returned HTTP {response.status}: {body[:200]}"
                )
            body = await api._json(response, endpoint=url)  # noqa: SLF001
        gateway_response = body.get("response") if isinstance(body, dict) else None
        if not isinstance(gateway_response, dict):
            raise ApiContractUnavailable("ConnectLife Android authorization returned an invalid response")
        if gateway_response.get("resultCode") not in (0, "0", None):
            raise ApiContractUnavailable(
                "ConnectLife Android authorization was rejected: "
                f"{gateway_response.get('errorCode')} {gateway_response.get('errorDesc')}"
            )
        return gateway_response

    @staticmethod
    def _android_result_token(response: dict[str, Any]) -> str:
        """Extract an access token without ever persisting it in integration data."""
        data = response.get("resultData", response)
        if isinstance(data, str) and data:
            return data
        if isinstance(data, dict):
            token = data.get("accessToken") or data.get("token")
            if isinstance(token, str) and token:
                return token
        raise ApiContractUnavailable("ConnectLife Android authorization returned no access token")

    async def _async_food_manager_access_token(self, api: Any, gateway_base_url: str) -> str:
        """Sign in using the Android CDC account flow."""
        authorize_base_url = self._food_manager_authorize_base_url(gateway_base_url)
        async with api._client_session() as session:  # noqa: SLF001 - installed client owns account login
            uid, _ = await api._login_to_gigya(session)  # noqa: SLF001
            async with session.post(api.jwt_url, data={
                "APIKey": api.api_key, "UID": uid,
                "userKey": self._CDC_USER_KEY, "secret": self._CDC_USER_SECRET,
            }) as response:
                if response.status != 200:
                    raise ApiContractUnavailable(f"ConnectLife CDC JWT request returned HTTP {response.status}")
                jwt_response = await api._json(response, endpoint=api.jwt_url)  # noqa: SLF001
            gigya_jwt = jwt_response.get("id_token")
            if not isinstance(gigya_jwt, str) or not gigya_jwt:
                raise ApiContractUnavailable("ConnectLife CDC JWT request returned no token")
            country = getattr(getattr(self._hass, "config", None), "country", None) or "UA"
            sign_in_response = await self._async_android_post(
                api,
                session,
                f"{authorize_base_url}/br/login",
                self._android_signed_request(
                    None,
                    {
                        "thirdType": "CDC",
                        "thirdClientId": uid,
                        "idToken": gigya_jwt,
                        "platformId": "5",
                        "countryCode": country,
                    },
                ), encrypted=False,
            )
        return self._android_result_token(sign_in_response)

    async def _async_write(self, path: str, payload: dict[str, Any]) -> Any:
        """Send a Food Manager write in the Android application's format."""
        api, gateway_base_url, _ = await self._async_api_and_refrigerator()
        access_token = await self._async_food_manager_access_token(api, gateway_base_url)
        request_data = self._android_signed_request(access_token, payload)
        async with api._client_session() as session:  # noqa: SLF001 - no public Food Manager API exists
            gateway_response = await self._async_android_post(
                api, session, f"{gateway_base_url}/{path}", request_data
            )
        return gateway_response.get("resultData", gateway_response)

    async def _async_food_request_v81(self, path: str, payload: dict[str, Any]) -> Any:
        """Send one v8.1 Android-format request with the renewable OAuth token.

        This deliberately does not use ``_async_food_manager_access_token``.
        The latter is a separate mobile-session bootstrap which is not part of
        the established gateway OAuth lifecycle used by this integration.
        Keeping this transport separate makes it safe to compare the v8.1
        contract with the working legacy create route.
        """
        api, gateway_base_url, _ = await self._async_api_and_refrigerator()
        await api._fetch_access_token()  # noqa: SLF001 - installed client renews its own OAuth token
        access_token = getattr(api, "_access_token", None)
        if not isinstance(access_token, str) or not access_token:
            raise ApiContractUnavailable("ConnectLife gateway did not provide an OAuth access token")
        request_data = self._android_signed_request(access_token, payload)
        async with api._client_session() as session:  # noqa: SLF001 - vendor has no public Food API
            gateway_response = await self._async_android_post(
                api, session, f"{gateway_base_url}/{path}", request_data
            )
        return gateway_response.get("resultData", gateway_response)

    async def async_shopping_items(self) -> list[ShoppingItem]:
        """Return live shopping items, preserving vendor IDs for future sync."""
        data = await self._async_get(self._SHOPPING_LIST_PATH)
        rows = self._shopping_rows(data)
        return [item for row in rows if (item := self._shopping_from_row(row))]

    async def async_shopping_lists(self) -> list[dict[str, Any]]:
        """Return list metadata without exposing tokens or purchase details."""
        lists = self._shopping_list_containers(
            await self._async_food_request(self._SHOPPING_LIST_PATH, {})
        )
        return [
            {
                "id": str(row["id"]),
                "name": str(row.get("shoppingListName") or row.get("name") or ""),
                "item_count": len(row.get("purchaseDataList") or []),
            }
            for row in lists
        ]

    async def async_create_shopping_list(self, shopping_list_name: str) -> dict[str, Any]:
        """Create one named empty list, or return it unchanged when it exists."""
        name = shopping_list_name.strip()
        if not name:
            raise ApiContractUnavailable("A shopping-list name is required")
        existing = next((row for row in await self.async_shopping_lists() if row["name"] == name), None)
        if existing is not None:
            return {"created": False, "list": existing}
        # ``addShoppingList`` accepts the OAuth gateway envelope plus this one
        # scalar field. Adding appliance context or an empty ``purchases``
        # array makes the vendor's signature validator reject the request.
        await self._async_food_request(
            self._ADD_SHOPPING_PATH,
            {"shoppingListName": name},
            include_device_context=False,
        )
        created = next((row for row in await self.async_shopping_lists() if row["name"] == name), None)
        if created is None:
            raise ApiContractUnavailable("ConnectLife did not return the created shopping list")
        return {"created": True, "list": created}

    async def async_create_shopping_list_v81(self, shopping_list_name: str) -> dict[str, Any]:
        """Create a list through the Android Food Manager v8.1 contract.

        This is intentionally an opt-in experimental route.  The established
        ``async_create_shopping_list`` remains the production path until this
        route has been independently verified against the vendor service.
        """
        name = shopping_list_name.strip()
        if not name:
            raise ApiContractUnavailable("A shopping-list name is required")
        existing = next((row for row in await self.async_shopping_lists() if row["name"] == name), None)
        if existing is not None:
            return {"created": False, "list": existing}
        # ShoppingListModel.toFormData() in Android always includes
        # ``purchases``.  For a new list it is an empty array.
        await self._async_food_request_v81(
            self._ADD_SHOPPING_PATH,
            {"shoppingListName": name, "purchases": []},
        )
        created = next((row for row in await self.async_shopping_lists() if row["name"] == name), None)
        if created is None:
            raise ApiContractUnavailable("ConnectLife did not return the created shopping list")
        return {"created": True, "list": created}

    async def async_delete_shopping_list(self, shopping_list_name: str) -> dict[str, Any]:
        """Delete exactly one named list and verify that it disappeared."""
        name = shopping_list_name.strip()
        if not name:
            raise ApiContractUnavailable("A shopping-list name is required")
        target = next((row for row in await self.async_shopping_lists() if row["name"] == name), None)
        if target is None:
            return {"deleted": False, "reason": "not_found", "name": name}
        await self._async_food_request(self._DELETE_SHOPPING_PATH, {"shoppingListIds": [target["id"]]})
        if any(row["id"] == target["id"] for row in await self.async_shopping_lists()):
            raise ApiContractUnavailable("ConnectLife still returned the deleted shopping list")
        return {"deleted": True, "name": name}

    @staticmethod
    def _purchase_form_data(purchase: dict[str, Any]) -> dict[str, Any]:
        """Rebuild Android's ``ShoppingListItemModel.toFormData`` payload."""
        payload = {
            key: purchase.get(key)
            for key in (
                "purchaseId",
                "status",
                "fridgePurchaseId",
                "foodId",
                "purchaseDate",
            )
        }
        payload.update(
            {
                "cusFoodName": purchase.get("cusFoodName"),
                "status": purchase.get("status", 0),
                "enteringSource": 0,
            }
        )
        if purchase.get("foodId") is not None:
            payload["foodId"] = purchase["foodId"]
        if purchase.get("commonName") is not None:
            payload["commonName"] = purchase["commonName"]
        return {key: value for key, value in payload.items() if value is not None}

    async def async_rename_shopping_list(
        self, shopping_list_id: str, shopping_list_name: str
    ) -> dict[str, Any]:
        """Rename one existing list through Android's update DTO and verify it.

        The method intentionally replays every existing purchase with its
        server IDs.  Android's ``ShoppingListModel.toFormData`` always sends
        ``purchases``; omitting it changes the signed request contract.
        """
        name = shopping_list_name.strip()
        if not shopping_list_id or not name:
            raise ApiContractUnavailable("A shopping-list ID and name are required")
        containers = self._shopping_list_containers(
            await self._async_food_request(self._SHOPPING_LIST_PATH, {})
        )
        target = next((row for row in containers if str(row.get("id")) == str(shopping_list_id)), None)
        if target is None:
            raise ApiContractUnavailable("ConnectLife shopping list was not found")
        request = {
            "shoppingListId": target["id"],
            "shoppingListName": name,
            "purchases": [
                self._purchase_form_data(row)
                for row in (target.get("purchaseDataList") or [])
                if isinstance(row, dict)
            ],
        }
        await self._async_write(self._UPDATE_SHOPPING_PATH, request)
        after = self._shopping_list_containers(
            await self._async_food_request(self._SHOPPING_LIST_PATH, {})
        )
        updated = next((row for row in after if str(row.get("id")) == str(shopping_list_id)), None)
        actual_name = "" if updated is None else str(updated.get("shoppingListName") or updated.get("name") or "")
        if actual_name != name:
            raise ApiContractUnavailable("ConnectLife did not return the renamed shopping list")
        return {"id": str(shopping_list_id), "old_name": str(target.get("shoppingListName") or target.get("name") or ""), "name": actual_name}

    @classmethod
    def _shopping_list_containers(cls, value: Any) -> list[dict[str, Any]]:
        """Return only list containers, never the nested purchase rows."""
        if isinstance(value, list):
            return [row for child in value for row in cls._shopping_list_containers(child)]
        if not isinstance(value, dict):
            return []
        if value.get("id") is not None and (
            "purchaseDataList" in value or "shoppingListName" in value or "name" in value
        ):
            return [value]
        return [row for child in value.values() for row in cls._shopping_list_containers(child)]

    @staticmethod
    def _purchase_payload(item: ShoppingItem) -> dict[str, Any]:
        """The minimal Android DTO accepted by updateShoppingList.

        ConnectLife's observed DTO contains no quantity field.  We preserve
        Grocy's quantity in the service response, but do not invent an
        unverified remote field which the gateway rejects.
        """
        return {
            "cusFoodName": item.name,
            "status": 0,
            "enteringSource": 0,
            "commonName": item.name,
            "purchaseDate": "",
        }

    async def async_sync_grocy_shopping(
        self, items: list[ShoppingItem], shopping_list_name: str | None
    ) -> dict[str, Any]:
        """Add Grocy items missing from one named ConnectLife shopping list.

        This is intentionally additive: existing ConnectLife purchases are
        retained, checked Grocy rows are ignored, and matching is normalized
        by name.  A caller must provide the destination list explicitly so a
        household's unrelated lists are never selected implicitly.
        """
        if not shopping_list_name or not shopping_list_name.strip():
            raise ApiContractUnavailable("A target ConnectLife shopping-list name is required")
        target_name = shopping_list_name.strip()
        api, _, refrigerator = await self._async_api_and_refrigerator()
        base = {"puid": self._device_id(refrigerator), "wifiId": self._wifi_id(refrigerator)}
        lists = self._shopping_list_containers(await self._async_get(self._SHOPPING_LIST_PATH, base))
        target = next(
            (row for row in lists if row.get("name") == target_name or row.get("shoppingListName") == target_name),
            None,
        )
        if target is None:
            await self._async_write(self._ADD_SHOPPING_PATH, {"shoppingListName": target_name, "purchases": []})
            lists = self._shopping_list_containers(await self._async_get(self._SHOPPING_LIST_PATH, base))
            target = next(
                (row for row in lists if row.get("name") == target_name or row.get("shoppingListName") == target_name),
                None,
            )
        if target is None or target.get("id") is None:
            raise ApiContractUnavailable("ConnectLife did not create the target shopping list")
        existing = list(target.get("purchaseDataList") or [])
        existing_names = {
            normalized_name(str(row.get("cusFoodName") or row.get("commonName") or row.get("foodName") or ""))
            for row in existing
        }
        pending = [item for item in items if not item.checked and normalized_name(item.name) not in existing_names]
        if not pending:
            return {"shopping_list_name": target_name, "added": [], "added_count": 0,
                    "skipped_existing": [item.name for item in items], "skipped_count": len(items)}
        request = {
            **base,
            "shoppingListId": target["id"],
            "shoppingListName": str(target.get("name") or target.get("shoppingListName") or target_name),
            "purchases": [*existing, *(self._purchase_payload(item) for item in pending)],
        }
        await self._async_write(self._UPDATE_SHOPPING_PATH, request)
        after_lists = self._shopping_list_containers(await self._async_get(self._SHOPPING_LIST_PATH, base))
        updated = next((row for row in after_lists if row.get("id") == target["id"]), None)
        updated_names = {
            normalized_name(str(row.get("cusFoodName") or row.get("commonName") or row.get("foodName") or ""))
            for row in (updated or {}).get("purchaseDataList", [])
        }
        missing = [item.name for item in pending if normalized_name(item.name) not in updated_names]
        if missing:
            raise ApiContractUnavailable(f"ConnectLife did not return synced shopping items: {', '.join(missing)}")
        skipped = [item.name for item in items if item not in pending]
        return {"shopping_list_name": target_name, "added": [item.name for item in pending],
                "added_count": len(pending), "skipped_existing": skipped, "skipped_count": len(skipped)}

    async def async_inventory_items(self) -> list[InventoryItem]:
        """Return live fridge inventory, normalized for Grocy mapping."""
        data = await self._async_get(self._INVENTORY_PATH)
        rows = self._inventory_rows(data)
        return [item for row in rows if (item := self._inventory_from_row(row))]

    async def async_inventory_rows(self) -> list[dict[str, Any]]:
        """Return raw inventory DTOs with inherited room information.

        This stays internal to the integration: services return normalized data
        only, while writes need the vendor's room IDs and food metadata.
        """
        return self._inventory_rows(await self._async_get(self._INVENTORY_PATH))

    @classmethod
    def _inventory_write_payload(
        cls, item: InventoryItem, *, room: dict[str, Any], food: dict[str, Any] | None = None,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the field set used by Android's FridgeFoodModel DTO.

        ``food`` supplies library metadata for recognised products; custom
        names deliberately omit ``foodId``. Existing records retain server
        fields required by the update endpoint.
        """
        source = {**(food or {}), **(existing or {})}
        payload = {
            "foodId": source.get("foodId"),
            "foodName": source.get("foodName") or item.name,
            "commonName": source.get("commonName") or item.name,
            "cusFoodName": item.name,
            "foodNum": item.quantity,
            "foodUnitId": source.get("foodUnitId") or source.get("defaultUnitId"),
            "foodUnitCode": item.unit or source.get("foodUnitCode"),
            "roomId": room.get("roomId"),
            "roomCode": room.get("roomCode"),
            "invalidTime": item.best_before or source.get("invalidTime"),
            "foodSaveDate": source.get("foodSaveDate"),
            "enteringSource": source.get("enteringSource", 0),
        }
        if existing and existing.get("fridgeFoodId") is not None:
            payload["fridgeFoodId"] = existing["fridgeFoodId"]
        return {key: value for key, value in payload.items() if value is not None}

    @classmethod
    def _room_for_zone(cls, rows: list[dict[str, Any]], zone: FoodZone) -> dict[str, Any]:
        for row in rows:
            if cls._zone(row.get("roomCode")) == zone and row.get("roomId") is not None:
                return {"roomId": row["roomId"], "roomCode": row.get("roomCode")}
        raise ApiContractUnavailable(f"ConnectLife has no usable room for {zone.value}")

    async def async_add_inventory_item(self, item: InventoryItem) -> dict[str, Any]:
        """Add a Grocy item to the mapped ConnectLife refrigerator zone."""
        rows = await self.async_inventory_rows()
        room = self._room_for_zone(rows, item.zone)
        catalog_food = next(
            (
                food for genre in await self.async_catalog()
                for food in genre.get("foods", [])
                if normalized_name(str(food.get("foodName") or "")) == normalized_name(item.name)
            ),
            None,
        )
        payload = self._inventory_write_payload(item, room=room, food=catalog_food)
        await self._async_write(self._ADD_INVENTORY_PATH, payload)
        after = await self.async_inventory_rows()
        matches = [
            row for row in after
            if normalized_name(str(row.get("cusFoodName") or row.get("commonName") or row.get("foodName") or ""))
            == normalized_name(item.name)
            and self._zone(row.get("roomCode")) == item.zone
        ]
        if not matches:
            raise ApiContractUnavailable(f"ConnectLife did not return added inventory item: {item.name}")
        created = matches[-1]
        return {
            "name": item.name,
            "zone": item.zone.value,
            "fridge_food_id": str(created.get("fridgeFoodId") or created.get("foodId") or ""),
        }

    async def async_delete_inventory_item(self, fridge_food_id: str) -> dict[str, Any]:
        """Delete one inventory record and verify that its exact ID vanished."""
        await self._async_write(self._DELETE_INVENTORY_PATH, {"fridgeFoodIds": [fridge_food_id]})
        rows = await self.async_inventory_rows()
        if any(str(row.get("fridgeFoodId") or row.get("foodId") or "") == fridge_food_id for row in rows):
            raise ApiContractUnavailable(f"ConnectLife still returned inventory item {fridge_food_id}")
        return {"deleted": fridge_food_id, "verified": True}

    async def async_sync_inventory_from_grocy(self, items: list[InventoryItem]) -> dict[str, Any]:
        """Add Grocy stock missing from the mapped ConnectLife zones.

        Existing user records are left untouched until update and conflict
        semantics are independently validated. This makes the first reverse
        synchronization safe and idempotent.
        """
        existing = await self.async_inventory_items()
        existing_keys = {(normalized_name(item.name), item.zone) for item in existing}
        added: list[str] = []
        skipped: list[str] = []
        for item in items:
            key = (normalized_name(item.name), item.zone)
            if key in existing_keys:
                skipped.append(item.name)
                continue
            await self.async_add_inventory_item(item)
            existing_keys.add(key)
            added.append(item.name)
        return {"added": added, "added_count": len(added), "skipped_existing": skipped,
                "skipped_count": len(skipped)}

    async def async_test_inventory_roundtrip(self) -> dict[str, Any]:
        """Create and remove one clearly-labelled refrigerator test item."""
        item = InventoryItem("[HA test] Grocy ConnectLife Sync inventory", FoodZone.FRIDGE, 1)
        created = await self.async_add_inventory_item(item)
        identifier = created["fridge_food_id"]
        if not identifier:
            raise ApiContractUnavailable("ConnectLife returned no ID for the temporary inventory item")
        try:
            verified_added = True
        finally:
            await self.async_delete_inventory_item(identifier)
        return {"created": created, "verified_added": verified_added, "verified_deleted": True}

    async def async_catalog(self) -> list[dict[str, Any]]:
        api, gateway_base_url, refrigerator = await self._async_api_and_refrigerator()
        data = await api._request_gateway_json(  # noqa: SLF001
            f"{gateway_base_url}/{self._FOOD_CATALOG_PATH}",
            payload={"puid": self._device_id(refrigerator), "wifiId": self._wifi_id(refrigerator)},
            retry_on_reauth=True, retry_on_randstr=True,
        )
        return self._find_rows(data.get("resultData", data), "food")

    async def async_units(self) -> list[dict[str, Any]]:
        api, gateway_base_url, refrigerator = await self._async_api_and_refrigerator()
        data = await api._request_gateway_json(  # noqa: SLF001
            f"{gateway_base_url}/{self._UNITS_PATH}",
            payload={"puid": self._device_id(refrigerator), "wifiId": self._wifi_id(refrigerator), "numericCode": "804"},
            retry_on_reauth=True, retry_on_randstr=True,
        )
        return self._find_rows(data.get("resultData", data), "unit")


    async def async_delete_shopping_list_for_test(self, shopping_list_name: str) -> dict[str, Any]:
        """Delete exactly one named temporary list and verify its absence."""
        api, _, refrigerator = await self._async_api_and_refrigerator()
        base = {"puid": self._device_id(refrigerator), "wifiId": self._wifi_id(refrigerator)}
        lists = self._find_rows(await self._async_get(self._SHOPPING_LIST_PATH, base), "shopping")
        target = next((row for row in lists if row.get("name") == shopping_list_name), None)
        if target is None or target.get("id") is None:
            raise ApiContractUnavailable(f"Shopping list {shopping_list_name!r} was not found")
        await self._async_write(self._DELETE_SHOPPING_PATH, {"shoppingListIds": [target["id"]]})
        after = self._find_rows(await self._async_get(self._SHOPPING_LIST_PATH, base), "shopping")
        if any(row.get("id") == target["id"] for row in after):
            raise ApiContractUnavailable("ConnectLife did not delete the temporary shopping list")
        return {"deleted": shopping_list_name, "verified": True}

    async def async_test_shopping_roundtrip(self, shopping_list_name: str | None = None) -> dict[str, Any]:
        """Prove the discovered shopping-list write contract without leaving data behind.

        The Android ConnectLife app (package ``com.hisense.juconnect.connectlife``)
        contains the two endpoint names and DTO fields used below.  This test
        proceeds only on an empty list, adds one catalogue food, reads back its
        server-issued identifier, deletes that exact identifier, and verifies
        that the list is empty again.
        """
        if not shopping_list_name or not shopping_list_name.strip():
            raise ApiContractUnavailable("A target ConnectLife shopping-list name is required")
        api, _, refrigerator = await self._async_api_and_refrigerator()
        base = {"puid": self._device_id(refrigerator), "wifiId": self._wifi_id(refrigerator)}
        before = self._find_rows(await self._async_get(self._SHOPPING_LIST_PATH, base), "shopping")
        target = next(
            (
                row for row in before
                if row.get("name") == shopping_list_name.strip()
                or row.get("shoppingListName") == shopping_list_name.strip()
            ),
            None,
        )
        target_name = shopping_list_name.strip()
        if target is None:
            # Android sends ShoppingListModel.toFormData() to this endpoint.
            await self._async_write(
                self._ADD_SHOPPING_PATH,
                {"shoppingListName": target_name, "purchases": []},
            )
            before = self._find_rows(await self._async_get(self._SHOPPING_LIST_PATH, base), "shopping")
            target = next(
                (
                    row for row in before
                    if row.get("name") == shopping_list_name.strip()
                    or row.get("shoppingListName") == shopping_list_name.strip()
                ),
                None,
            )
        if target is None or target.get("id") is None:
            raise ApiContractUnavailable("ConnectLife did not create the target shopping list")

        candidate = next(
            (
                food
                for genre in await self.async_catalog()
                for food in genre.get("foods", [])
                if food.get("foodCode") and food.get("foodName")
            ),
            None,
        )
        if candidate is None:
            raise ApiContractUnavailable("ConnectLife food catalogue has no usable test item")
        purchase_base = {
            **base,
            "shoppingListId": target["id"],
            "shoppingListName": target["name"],
        }
        if target.get("purchaseDataList"):
            # A prior test left a temporary item behind.  The appliance's
            # reset endpoint does not clear it and an empty update is rejected
            # by this backend's signature validator.  The Android app's
            # documented list delete endpoint accepts just a list of IDs.
            await self._async_write(self._DELETE_SHOPPING_PATH, {"shoppingListIds": [target["id"]]})
            await self._async_write(
                self._ADD_SHOPPING_PATH,
                {"shoppingListName": target_name, "purchases": []},
            )
            lists = self._find_rows(await self._async_get(self._SHOPPING_LIST_PATH, base), "shopping")
            target = next((row for row in lists if row.get("name") == target_name), None)
            if target is None or target.get("purchaseDataList"):
                raise ApiContractUnavailable("Shopping-list roundtrip requires an empty target list")
            purchase_base = {"shoppingListId": target["id"], "shoppingListName": target["name"]}
        # Recovered from the Android AOT functions
        # ``ShoppingListModel.toFormData`` and
        # ``ShoppingListItemModel.toFormData``.  The app updates the whole
        # list through ``fridge/updateShoppingList``; it does not use
        # ``fridge/add_purchase`` for a shopping-list item.
        # Exact field set emitted by ShoppingListItemModel.toFormData in the
        # Android application. The backend rejects unknown item fields.
        purchase = {
            "cusFoodName": candidate["foodName"],
            "status": 0,
            "enteringSource": 0,
            "foodId": candidate.get("foodId"),
            "commonName": candidate["foodName"],
            "purchaseDate": "",
        }
        request = {**purchase_base, "purchases": [
            {key: value for key, value in purchase.items() if value is not None}
        ]}
        await self._async_write(self._UPDATE_SHOPPING_PATH, request)

        try:
            lists = self._find_rows(await self._async_get(self._SHOPPING_LIST_PATH, base), "shopping")
            updated = next((row for row in lists if row.get("id") == target["id"]), None)
            purchases = (updated or {}).get("purchaseDataList") or []
            matching = [row for row in purchases if row.get("cusFoodName") == candidate["foodName"]]
            if len(matching) != 1:
                raise ApiContractUnavailable("Could not identify the temporary ConnectLife shopping item")
        finally:
            # The same documented Android operations restore an empty list
            # with the user-facing name unchanged.
            await self._async_write(self._DELETE_SHOPPING_PATH, {"shoppingListIds": [target["id"]]})
            await self._async_write(
                self._ADD_SHOPPING_PATH,
                {"shoppingListName": target_name, "purchases": []},
            )
        lists = self._find_rows(await self._async_get(self._SHOPPING_LIST_PATH, base), "shopping")
        reset = next((row for row in lists if row.get("name") == target_name), None)
        if (reset or {}).get("purchaseDataList"):
            raise ApiContractUnavailable("ConnectLife did not remove the temporary shopping item")
        return {"created_and_removed": candidate["foodName"], "cleanup_verified": True,
                "write_contract": "fridge/updateShoppingList", "request_fields": sorted(request)}

    async def async_probe_shopping_list(self) -> dict[str, Any]:
        return await self._async_probe_path(self._SHOPPING_LIST_PATH)

    async def async_probe_inventory(self) -> dict[str, Any]:
        return await self._async_probe_path(self._INVENTORY_PATH)

    async def async_probe_food_catalog(self) -> dict[str, Any]:
        rows = await self.async_catalog()
        return {"item_count": len(rows), "response_fields": sorted({key for row in rows for key in row}),
                "response_schema": self._schema(rows)}

    async def async_probe_units(self) -> dict[str, Any]:
        rows = await self.async_units()
        return {"item_count": len(rows), "response_fields": sorted({key for row in rows for key in row}),
                "response_schema": self._schema(rows)}

    async def _async_probe_path(self, path: str) -> dict[str, Any]:
        result = await self._async_get(path)
        rows = self._find_rows(result, "")
        api, gateway_base_url, _ = await self._async_api_and_refrigerator()
        return {"transport_client": type(api).__name__, "gateway_base_url": gateway_base_url,
                "item_count": len(rows), "response_fields": sorted({key for row in rows for key in row}),
                "response_schema": self._schema(result)}

    @staticmethod
    def _find_rows(value: Any, marker: str) -> list[dict[str, Any]]:
        """Flatten vendor response enough to find its first list of object rows."""
        if isinstance(value, list):
            if all(isinstance(row, dict) for row in value):
                # Both inventory and catalog wrap product rows in room/genre rows.
                nested = [
                    row.get("foods") or row.get("fridgeFoods")
                    for row in value
                    if isinstance(row.get("foods") or row.get("fridgeFoods"), list)
                ]
                if nested and len(nested) == len(value):
                    return [food for foods in nested for food in foods if isinstance(food, dict)]
                return value
            for child in value:
                rows = ConnectLifeFoodClient._find_rows(child, marker)
                if rows:
                    return rows
        if isinstance(value, dict):
            preferred = [key for key in value if marker and marker.lower() in key.lower()]
            for key in [*preferred, *[key for key in value if key not in preferred]]:
                rows = ConnectLifeFoodClient._find_rows(value[key], marker)
                if rows:
                    return rows
        return []

    @classmethod
    def _inventory_rows(cls, value: Any, inherited_room: Any = None) -> list[dict[str, Any]]:
        """Flatten inventory while retaining the enclosing refrigerator zone.

        ConnectLife puts ``roomCode`` on a room object and its foods in a
        nested ``fridgeFoods`` list.  Preserving that association is essential:
        Grocy must never put freezer stock into the fridge location.
        """
        if isinstance(value, list):
            return [row for child in value for row in cls._inventory_rows(child, inherited_room)]
        if not isinstance(value, dict):
            return []

        room = value.get("roomCode", inherited_room)
        foods = value.get("fridgeFoods")
        if isinstance(foods, list):
            return [
                {**food, "roomCode": food.get("roomCode") or room}
                for food in foods if isinstance(food, dict)
            ]
        return [row for child in value.values() for row in cls._inventory_rows(child, room)]

    @classmethod
    def _shopping_rows(cls, value: Any) -> list[dict[str, Any]]:
        """Flatten ``purchaseDataList`` while retaining the parent list ID.

        ``getShoppingList`` returns shopping-list containers, each with its
        actual products in ``purchaseDataList``.  Treating the containers as
        products made the earlier read service report the list name as an
        item.  The Android DTO uses ``purchaseId`` for the stable item ID.
        """
        if isinstance(value, list):
            return [row for child in value for row in cls._shopping_rows(child)]
        if not isinstance(value, dict):
            return []
        purchases = value.get("purchaseDataList")
        if isinstance(purchases, list):
            return [
                {
                    **purchase,
                    "shoppingListId": purchase.get("shoppingListId", value.get("id")),
                    "shoppingListName": value.get("name"),
                }
                for purchase in purchases
                if isinstance(purchase, dict)
            ]
        return [row for child in value.values() for row in cls._shopping_rows(child)]

    @staticmethod
    def _number(value: Any, default: float = 1) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _zone(value: Any) -> FoodZone:
        text = str(value or "").casefold()
        if "freez" in text:
            return FoodZone.FREEZER
        if "fresh" in text or "ice" in text:
            return FoodZone.MY_FRESH_CHOICE
        return FoodZone.FRIDGE

    @classmethod
    def _inventory_from_row(cls, row: dict[str, Any]) -> InventoryItem | None:
        name = row.get("cusFoodName") or row.get("commonName") or row.get("foodName")
        if not isinstance(name, str) or not name.strip():
            return None
        return InventoryItem(name=name.strip(), zone=cls._zone(row.get("roomCode")),
            quantity=cls._number(row.get("foodNum")), unit=row.get("foodUnitCode"),
            best_before=row.get("invalidTime"), vendor_id=str(row.get("fridgeFoodId") or row.get("foodId") or "") or None)

    @classmethod
    def _shopping_from_row(cls, row: dict[str, Any]) -> ShoppingItem | None:
        name = row.get("foodName") or row.get("cusFoodName") or row.get("commonName")
        if not isinstance(name, str) or not name.strip():
            return None
        return ShoppingItem(name=name.strip(), quantity=cls._number(row.get("foodNum") or row.get("num")),
            unit=row.get("foodUnitCode") or row.get("unitName"),
            checked=bool(row.get("collected") or row.get("checked") or row.get("status") == 1),
            vendor_id=str(row.get("purchaseId") or row.get("shoppingId") or row.get("id") or "") or None)

    @classmethod
    def _schema(cls, value: Any, depth: int = 0) -> Any:
        if depth >= 5:
            return type(value).__name__
        if isinstance(value, dict):
            return {key: cls._schema(child, depth + 1) for key, child in sorted(value.items())}
        if isinstance(value, list):
            return [cls._schema(value[0], depth + 1)] if value else []
        return type(value).__name__
