import asyncio
import unittest
from hisense_food_manager.coordinator import InventoryCoordinator
from hisense_food_manager.models import FoodZone, InventoryItem


class Store:
    def __init__(self): self.data = {}
    async def async_load(self): return self.data.copy()
    async def async_save(self, data): self.data = {**data, "inventory": dict(data["inventory"])}


class Side:
    def __init__(self, quantity): self.quantity = quantity; self.writes = 0; self.fail = False
    async def async_inventory_items(self):
        return [] if self.quantity is None else [InventoryItem("Milk", FoodZone.FRIDGE, self.quantity)]
    async def async_sync_inventory_from_connectlife(self, items, **kwargs):
        if self.fail: raise RuntimeError("write failed")
        self.quantity = items[0].quantity; self.writes += 1
        return {"skipped_unmapped": []}
    async def async_sync_inventory_from_grocy(self, items):
        if self.fail: raise RuntimeError("write failed")
        self.quantity = items[0].quantity; self.writes += 1
        return {"conflicts": []}


class CoordinatorTests(unittest.TestCase):
    def test_both_directions_idempotence_and_conflict(self):
        async def run():
            left, right, store = Side(2), Side(None), Store()
            sync = InventoryCoordinator(left, right, store)
            self.assertEqual((await sync.async_sync())["to_connectlife"], ["fridge:milk"])
            self.assertEqual((await sync.async_sync())["unchanged"], ["fridge:milk"])
            self.assertEqual(right.writes, 1)
            right.quantity = 4
            self.assertEqual((await sync.async_sync())["to_grocy"], ["fridge:milk"])
            self.assertEqual(left.quantity, 4)
            left.quantity, right.quantity = 5, 6
            result = await sync.async_sync()
            self.assertEqual(result["conflicts"][0]["reason"], "conflict")
            self.assertEqual(store.data["inventory"]["fridge:milk"], 4)
            left.quantity = None
            self.assertEqual((await sync.async_sync())["conflicts"][0]["reason"], "delete_conflict")
        asyncio.run(run())

    def test_failed_write_does_not_advance_baseline(self):
        async def run():
            left, right, store = Side(2), Side(2), Store()
            sync = InventoryCoordinator(left, right, store)
            await sync.async_sync()
            right.quantity = 4; left.fail = True
            result = await sync.async_sync()
            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(store.data["inventory"]["fridge:milk"], 2)
        asyncio.run(run())
