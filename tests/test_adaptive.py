import asyncio
import copy
import unittest
from hisense_food_manager.adaptive import AdaptiveSync


class Store:
    def __init__(self): self.data = {}
    async def async_load(self): return copy.deepcopy(self.data)
    async def async_save(self, data): self.data = copy.deepcopy(data)


class Sync:
    def __init__(self): self.calls = 0; self.failed = False; self.conflicts = []
    async def async_sync(self, *args):
        self.calls += 1
        if self.failed: raise RuntimeError("offline")
        return {"errors": [], "conflicts": self.conflicts}


class AdaptiveTests(unittest.TestCase):
    def test_long_outage_caps_attempts_and_recovery_restores_minute(self):
        async def run():
            now = [1000]
            inv, shopping, store = Sync(), Sync(), Store()
            inv.failed = shopping.failed = True
            adaptive = AdaptiveSync(inv, shopping, store, lambda: now[0])
            for delay in (60, 120, 300, 600, 600, 600):
                result = await adaptive.async_run(1, "list")
                self.assertEqual(result["inventory"]["retry_in_seconds"], delay)
                self.assertEqual(result["shopping:1:list"]["retry_in_seconds"], delay)
                calls = inv.calls, shopping.calls
                now[0] += delay - 1
                waiting = await adaptive.async_run(1, "list")
                self.assertTrue(all(x["status"] == "waiting" for x in waiting.values()))
                self.assertEqual((inv.calls, shopping.calls), calls)
                now[0] += 1
            inv.failed = shopping.failed = False
            adaptive = AdaptiveSync(inv, shopping, store, lambda: now[0])
            recovered = await adaptive.async_run(1, "list")
            self.assertTrue(all(x["status"] == "ok" for x in recovered.values()))
            self.assertTrue(all(x["retry_in_seconds"] == 60 for x in recovered.values()))
        asyncio.run(run())

    def test_backoff_is_persistent_and_independent_then_recovers(self):
        async def run():
            now = [1000]
            inv, shopping, store = Sync(), Sync(), Store()
            inv.failed = True
            adaptive = AdaptiveSync(inv, shopping, store, lambda: now[0])
            first = await adaptive.async_run(1, "list")
            self.assertEqual(first["inventory"]["retry_in_seconds"], 60)
            self.assertEqual(first["shopping:1:list"]["status"], "ok")
            await adaptive.async_run(1, "list")
            self.assertEqual(inv.calls, 1)
            now[0] += 60
            second = await adaptive.async_run(1, "list")
            self.assertEqual(second["inventory"]["retry_in_seconds"], 120)
            now[0] += 60
            # A new instance still honors the saved due time after a restart.
            adaptive = AdaptiveSync(inv, shopping, store, lambda: now[0])
            self.assertEqual((await adaptive.async_run(1, "list"))["inventory"]["status"], "waiting")
            now[0] += 60
            third = await adaptive.async_run(1, "list")
            self.assertEqual(third["inventory"]["retry_in_seconds"], 300)
            now[0] += 300
            fourth = await adaptive.async_run(1, "list")
            self.assertEqual(fourth["inventory"]["retry_in_seconds"], 600)
            inv.failed = False
            now[0] += 600
            recovered = await adaptive.async_run(1, "list")
            self.assertEqual(recovered["inventory"]["status"], "ok")
            self.assertEqual(recovered["inventory"]["retry_in_seconds"], 60)
        asyncio.run(run())

    def test_conflict_is_not_a_connection_failure(self):
        async def run():
            inv, shopping, store = Sync(), Sync(), Store()
            inv.conflicts = [{"reason": "conflict"}]
            result = await AdaptiveSync(inv, shopping, store, lambda: 100).async_run(1, "list")
            self.assertEqual(result["inventory"]["status"], "ok")
            self.assertEqual(store.data["domains"]["inventory"]["failures"], 0)
        asyncio.run(run())

    def test_partial_failure_retains_full_response(self):
        class Partial(Sync):
            async def async_sync(self, *args):
                return {"errors": [{"key": "freezer", "error": "unmapped"}], "conflicts": ["milk"], "unchanged": ["tea"]}
        async def run():
            result = await AdaptiveSync(Partial(), Sync(), Store(), lambda: 100).async_run(1, "list")
            self.assertEqual(result["inventory"]["status"], "error")
            self.assertEqual(result["inventory"]["result"]["unchanged"], ["tea"])
            self.assertEqual(result["shopping:1:list"]["status"], "ok")
        asyncio.run(run())
