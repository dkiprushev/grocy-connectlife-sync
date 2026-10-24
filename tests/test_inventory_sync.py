"""Regression coverage for bidirectional refrigerator inventory sync."""
from __future__ import annotations

import asyncio
import unittest

from hisense_food_manager.connectlife import ConnectLifeFoodClient
from hisense_food_manager.grocy import GrocyClient
from hisense_food_manager.models import FoodZone, InventoryItem


class ConnectLifeInventorySyncTests(unittest.TestCase):
    def test_android_inventory_routes_are_explicit(self) -> None:
        self.assertEqual(ConnectLifeFoodClient._ADD_INVENTORY_PATH, "fridge/pu/add_fridge_food")
        self.assertEqual(ConnectLifeFoodClient._UPDATE_INVENTORY_PATH, "fridge/pu/update_fridge_food")
        self.assertEqual(ConnectLifeFoodClient._DELETE_INVENTORY_PATH, "fridge/pu/delete_fridge_food")

    def test_write_payload_keeps_room_and_food_dto_fields(self) -> None:
        payload = ConnectLifeFoodClient._inventory_write_payload(
            InventoryItem("Молоко", FoodZone.FRIDGE, 2, "pcs", 4),
            room={"roomId": 1, "roomCode": "refrigerator"},
            food={"foodId": 42, "foodName": "Молоко", "foodUnitId": 3},
        )
        self.assertEqual(payload["foodId"], 42)
        self.assertEqual(payload["foodNum"], 2)
        self.assertEqual(payload["roomId"], 1)
        self.assertEqual(payload["roomCode"], "refrigerator")
        self.assertNotIn("amount", payload)

    def test_reverse_sync_is_additive_and_idempotent(self) -> None:
        client = ConnectLifeFoodClient(None)
        added: list[str] = []

        async def existing() -> list[InventoryItem]:
            return [InventoryItem("Молоко", FoodZone.FRIDGE, 1)]

        async def add(item: InventoryItem) -> dict:
            added.append(item.name)
            return {"name": item.name, "fridge_food_id": "99"}

        client.async_inventory_items = existing
        client.async_add_inventory_item = add
        result = asyncio.run(client.async_sync_inventory_from_grocy([
            InventoryItem("Молоко", FoodZone.FRIDGE, 2),
            InventoryItem("Тестовая кола", FoodZone.FRIDGE, 1),
        ]))
        self.assertEqual(added, ["Тестовая кола"])
        self.assertEqual(result["added"], ["Тестовая кола"])
        self.assertEqual(result["skipped_existing"], ["Молоко"])


class GrocyInventorySyncTests(unittest.TestCase):
    def test_connectlife_to_grocy_sets_exact_stock_amount(self) -> None:
        client = GrocyClient(None, "http://grocy/", "test")
        writes: list[tuple[str, dict]] = []

        async def list_rows(entity: str) -> list[dict]:
            return {
                "products": [{"id": 4, "name": "Молоко"}],
                "locations": [{"id": 2, "name": "Fridge"}],
                "quantity_units": [{"id": 1, "name": "pcs"}],
            }[entity]

        async def request(method: str, path: str, **kwargs) -> dict | list:
            if method == "GET" and path == "/api/stock":
                return [{"product_id": 4, "location_id": 2, "amount": 1}]
            writes.append((path, kwargs["json"]))
            return {}

        client.async_list = list_rows
        client._request = request
        result = asyncio.run(client.async_sync_inventory_from_connectlife([
            InventoryItem("Молоко", FoodZone.FRIDGE, 3),
        ]))
        self.assertEqual(result["updated"], ["Молоко"])
        self.assertEqual(writes, [("/api/stock/products/4/inventory", {"new_amount": 3, "location_id": 2})])

    def test_connectlife_to_grocy_skips_an_unmapped_zone(self) -> None:
        client = GrocyClient(None, "http://grocy/", "test")

        async def list_rows(entity: str) -> list[dict]:
            return {
                "products": [], "locations": [{"id": 2, "name": "Fridge"}],
                "quantity_units": [{"id": 1, "name": "pcs"}],
            }[entity]

        async def request(method: str, path: str, **kwargs) -> dict | list:
            if path == "/api/stock":
                return []
            return {"created_object_id": 10}

        client.async_list = list_rows
        client._request = request
        result = asyncio.run(client.async_sync_inventory_from_connectlife([
            InventoryItem("Пицца", FoodZone.FREEZER, 1),
        ]))
        self.assertEqual(result["skipped_unmapped"], [{"name": "Пицца", "location": "Freezer"}])

