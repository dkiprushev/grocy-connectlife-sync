import unittest
import asyncio

from hisense_food_manager.connectlife import ApiContractUnavailable, ConnectLifeFoodClient
from hisense_food_manager.models import FoodZone, ShoppingItem


class ConnectLifeModelTests(unittest.TestCase):
    def test_shopping_read_requires_unambiguous_list(self):
        client = ConnectLifeFoodClient(None)
        async def fake_get(path, payload=None):
            return {"shoppingLists": [
                {"id": 1, "name": "One", "purchaseDataList": [{"cusFoodName": "Milk"}]},
                {"id": 2, "name": "Two", "purchaseDataList": [{"cusFoodName": "Tea"}]},
            ]}
        client._async_get = fake_get
        with self.assertRaises(ApiContractUnavailable):
            asyncio.run(client.async_shopping_items())
        self.assertEqual([x.name for x in asyncio.run(client.async_shopping_items("Two"))], ["Tea"])
        with self.assertRaises(ApiContractUnavailable):
            asyncio.run(client.async_shopping_items("Missing"))

    def test_duplicate_target_names_fail_before_write(self):
        client = ConnectLifeFoodClient(None)
        async def fake_api():
            return object(), "https://gateway.example", type("Fridge", (), {"puid": "p", "wifi_id": "w"})()
        async def fake_get(path, payload=None):
            return [{"id": 1, "name": "Same", "purchaseDataList": []},
                    {"id": 2, "name": "Same", "purchaseDataList": []}]
        client._async_api_and_refrigerator = fake_api
        client._async_get = fake_get
        with self.assertRaises(ApiContractUnavailable):
            asyncio.run(client.async_sync_grocy_shopping([ShoppingItem("Milk")], "Same"))

    def test_inventory_row_is_normalized(self):
        item = ConnectLifeFoodClient._inventory_from_row({
            "cusFoodName": "Молоко", "roomCode": "freezer", "foodNum": "2",
            "foodUnitCode": "pcs", "invalidTime": "2026-10-01", "fridgeFoodId": 18,
        })
        self.assertEqual(item.name, "Молоко")
        self.assertEqual(item.zone, FoodZone.FREEZER)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.vendor_id, "18")

    def test_nested_response_rows_are_found(self):
        rows = ConnectLifeFoodClient._find_rows({"fridgeRooms": [{"fridgeFoods": [{"foodId": 1}]}]}, "fridge")
        self.assertEqual(rows, [{"foodId": 1}])

    def test_inventory_rows_inherit_the_enclosing_room_code(self):
        rows = ConnectLifeFoodClient._inventory_rows({"fridgeRooms": [
            {"roomCode": "freezer", "fridgeFoods": [{"foodId": 1, "foodName": "Ice cream"}]},
            {"roomCode": "fridge", "fridgeFoods": [{"foodId": 2, "foodName": "Milk", "roomCode": "fresh"}]},
        ]})
        self.assertEqual(rows[0]["roomCode"], "freezer")
        self.assertEqual(rows[1]["roomCode"], "fresh")
        self.assertEqual(ConnectLifeFoodClient._inventory_from_row(rows[0]).zone, FoodZone.FREEZER)
        self.assertEqual(ConnectLifeFoodClient._inventory_from_row(rows[1]).zone, FoodZone.MY_FRESH_CHOICE)

    def test_inventory_rows_inherit_room_when_food_dto_contains_null_room_code(self):
        rows = ConnectLifeFoodClient._inventory_rows({"fridgeRooms": [
            {"roomCode": "refrigerator", "roomId": 2, "fridgeFoods": [
                {"foodId": 1, "foodName": "Milk", "roomCode": None},
            ]},
        ]})
        self.assertEqual(rows[0]["roomCode"], "refrigerator")

    def test_shopping_row_is_normalized(self):
        item = ConnectLifeFoodClient._shopping_from_row({"foodName": "Яблоки", "num": 3, "checked": 1, "id": 7})
        self.assertEqual(item.name, "Яблоки")
        self.assertEqual(item.quantity, 3)
        self.assertTrue(item.checked)
        self.assertEqual(item.vendor_id, "7")

    def test_shopping_rows_keep_only_purchase_items(self):
        rows = ConnectLifeFoodClient._shopping_rows({"shoppingLists": [{
            "id": 12, "name": "Покупки", "purchaseDataList": [{
                "purchaseId": 77, "cusFoodName": "Молоко", "status": 0,
            }],
        }]})
        self.assertEqual(rows[0]["shoppingListId"], 12)
        self.assertEqual(rows[0]["shoppingListName"], "Покупки")
        item = ConnectLifeFoodClient._shopping_from_row(rows[0])
        self.assertEqual(item.name, "Молоко")
        self.assertEqual(item.vendor_id, "77")

    def test_android_verified_shopping_write_paths_are_explicit(self):
        self.assertEqual(ConnectLifeFoodClient._ADD_SHOPPING_PATH, "fridge/addShoppingList")
        self.assertEqual(ConnectLifeFoodClient._DELETE_SHOPPING_PATH, "fridge/delShoppingList")

    def test_grocy_purchase_payload_uses_only_verified_android_fields(self):
        payload = ConnectLifeFoodClient._purchase_payload(ShoppingItem("Кола", 2, "шт"))
        self.assertEqual(payload["cusFoodName"], "Кола")
        self.assertEqual(payload["commonName"], "Кола")
        self.assertNotIn("quantity", payload)
        self.assertNotIn("amount", payload)

    def test_shopping_list_containers_excludes_nested_purchase_rows(self):
        containers = ConnectLifeFoodClient._shopping_list_containers({"shoppingLists": [{
            "id": 12, "name": "Grocy", "purchaseDataList": [{"purchaseId": 7, "cusFoodName": "Кола"}],
        }]})
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]["id"], 12)

    def test_shopping_list_lifecycle_uses_gateway_oauth_transport(self):
        client = ConnectLifeFoodClient(None)
        state = {"shoppingLists": []}
        calls = []

        async def fake_food_request(path, payload, **kwargs):
            calls.append((path, payload, kwargs))
            if path == ConnectLifeFoodClient._SHOPPING_LIST_PATH:
                return state
            if path == ConnectLifeFoodClient._ADD_SHOPPING_PATH:
                state["shoppingLists"].append({
                    "id": 42, "shoppingListName": payload["shoppingListName"], "purchaseDataList": [],
                })
                return {}
            if path == ConnectLifeFoodClient._DELETE_SHOPPING_PATH:
                state["shoppingLists"] = [
                    row for row in state["shoppingLists"] if str(row["id"]) not in payload["shoppingListIds"]
                ]
                return {}
            raise AssertionError(path)

        client._async_food_request = fake_food_request
        created = asyncio.run(client.async_create_shopping_list("codex test"))
        self.assertEqual(created, {"created": True, "list": {"id": "42", "name": "codex test", "item_count": 0}})
        self.assertEqual(asyncio.run(client.async_shopping_lists())[0]["name"], "codex test")
        deleted = asyncio.run(client.async_delete_shopping_list("codex test"))
        self.assertEqual(deleted, {"deleted": True, "name": "codex test"})
        self.assertEqual(asyncio.run(client.async_shopping_lists()), [])
        self.assertIn(ConnectLifeFoodClient._ADD_SHOPPING_PATH, [path for path, _, _ in calls])
        self.assertIn(ConnectLifeFoodClient._DELETE_SHOPPING_PATH, [path for path, _, _ in calls])
        self.assertIn(
            (ConnectLifeFoodClient._ADD_SHOPPING_PATH,
             {"shoppingListName": "codex test"}, {"include_device_context": False}),
            calls,
        )
        self.assertIn(
            (ConnectLifeFoodClient._DELETE_SHOPPING_PATH, {"shoppingListIds": ["42"]}, {}),
            calls,
        )

    def test_v81_create_route_is_separate_and_uses_android_list_dto(self):
        client = ConnectLifeFoodClient(None)
        state = {"shoppingLists": []}
        calls = []

        async def fake_food_request(path, _payload, **_kwargs):
            if path != ConnectLifeFoodClient._SHOPPING_LIST_PATH:
                raise AssertionError(path)
            return state

        async def fake_v81_request(path, payload):
            calls.append((path, payload))
            state["shoppingLists"].append({
                "id": 43,
                "shoppingListName": payload["shoppingListName"],
                "purchaseDataList": payload["purchases"],
            })
            return {}

        client._async_food_request = fake_food_request
        client._async_food_request_v81 = fake_v81_request
        created = asyncio.run(client.async_create_shopping_list_v81("codex v81"))

        self.assertEqual(
            created,
            {"created": True, "list": {"id": "43", "name": "codex v81", "item_count": 0}},
        )
        self.assertEqual(
            calls,
            [(ConnectLifeFoodClient._ADD_SHOPPING_PATH, {"shoppingListName": "codex v81", "purchases": []})],
        )

    def test_grocy_sync_is_additive_and_requires_named_destination(self):
        import inspect
        source = inspect.getsource(ConnectLifeFoodClient.async_sync_grocy_shopping)
        self.assertIn('A target ConnectLife shopping-list name is required', source)
        self.assertIn('normalized_name(item.name)', source)
        self.assertIn('purchaseDataList', source)
        self.assertIn('async_write(self._UPDATE_SHOPPING_PATH', source)

    def test_grocy_sync_adds_only_missing_unchecked_items_and_verifies_readback(self):
        client = ConnectLifeFoodClient(None)
        writes = []
        state = {"shoppingLists": [{
            "id": 12, "name": "Grocy", "purchaseDataList": [
                {"cusFoodName": "Молоко", "status": 0},
            ],
        }]}

        async def fake_api_and_refrigerator():
            return object(), "https://gateway.example", type("Fridge", (), {"puid": "p", "wifi_id": "w"})()

        async def fake_get(path, payload=None):
            return state

        async def fake_write(path, payload):
            writes.append((path, payload))
            state["shoppingLists"][0]["purchaseDataList"] = payload["purchases"]
            return {}

        client._async_api_and_refrigerator = fake_api_and_refrigerator
        client._async_get = fake_get
        client._async_write = fake_write
        result = asyncio.run(client.async_sync_grocy_shopping([
            ShoppingItem("Молоко", 1), ShoppingItem("Кола", 2), ShoppingItem("Готово", 1, checked=True),
        ], "Grocy"))
        self.assertEqual(result["added"], ["Кола"])
        self.assertEqual(writes[0][0], ConnectLifeFoodClient._UPDATE_SHOPPING_PATH)
        self.assertEqual([row["cusFoodName"] for row in writes[0][1]["purchases"]], ["Молоко", "Кола"])
