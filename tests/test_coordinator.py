import asyncio
import copy
import unittest
from hisense_food_manager.coordinator import InventoryCoordinator, ShoppingCoordinator
from hisense_food_manager.models import FoodZone, InventoryItem, ShoppingItem


class Store:
    def __init__(self): self.data = {}
    async def async_load(self): return self.data.copy()
    async def async_save(self, data): self.data = copy.deepcopy(data)


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


class ShoppingTests(unittest.TestCase):
    def test_bidirectional_add_and_deletion_conflict(self):
        class ShoppingSide:
            def __init__(self, names): self.items = [ShoppingItem(name) for name in names]
            async def async_list(self, entity): return [{"id": 1}]
            async def async_shopping_items(self, selection): return self.items[:]
            async def async_import_shopping_list(self, items, selection): self.items.extend(items)
            async def async_sync_grocy_shopping(self, items, selection): self.items.extend(items)
        async def run():
            left, right, store = ShoppingSide(["Milk"]), ShoppingSide(["Tea"]), Store()
            coordinator = ShoppingCoordinator(left, right, store)
            result = await coordinator.async_sync(1, "Test")
            self.assertEqual(result["to_connectlife"], ["milk"])
            self.assertEqual(result["to_grocy"], ["tea"])
            self.assertEqual((await coordinator.async_sync(1, "Test"))["unchanged"], ["milk", "tea"])
            right.items = [item for item in right.items if item.name != "Milk"]
            self.assertEqual((await coordinator.async_sync(1, "Test"))["conflicts"][0]["reason"], "delete_conflict")
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
