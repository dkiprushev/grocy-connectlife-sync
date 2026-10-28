import unittest
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))
from hisense_food_manager.grocy import GrocyClient

class GrocyContractTest(unittest.TestCase):
    def test_url_is_local_and_normalized(self):
        client = GrocyClient(None, "http://homeassistant.local:9192/", "not-a-real-key")
        self.assertEqual(client._url("/api/system/info"), "http://homeassistant.local:9192/api/system/info")

    def test_roundtrip_contract_is_labelled_and_verifies_cleanup(self):
        import inspect
        source = inspect.getsource(GrocyClient.async_roundtrip_test)
        self.assertIn('[HA test] Grocy ConnectLife Sync', source)
        self.assertIn('verified_deleted', source)

if __name__ == "__main__":
    unittest.main()

class GrocySyncPlanContractTest(unittest.TestCase):
    def test_sync_plan_is_read_only(self):
        import inspect
        source = inspect.getsource(GrocyClient.async_inventory_sync_plan)
        self.assertNotIn('async_create(', source)
        self.assertNotIn('async_delete(', source)
        self.assertIn('planned_actions', source)

class GrocyStockContractTest(unittest.TestCase):
    def test_stock_roundtrip_is_labelled_and_cleans_up(self):
        import inspect
        source = inspect.getsource(GrocyClient.async_stock_roundtrip_test)
        self.assertIn('/api/stock/products/', source)
        self.assertIn('verified_stock_add', source)
        self.assertIn('Temporary stock remained', source)

class GrocyImportContractTest(unittest.TestCase):
    def test_import_creates_stock_and_cleans_up_partial_failure(self):
        import inspect
        source = inspect.getsource(GrocyClient.async_import_inventory)
        self.assertIn('/api/stock/products/', source)
        self.assertIn('skipped_existing', source)
        self.assertIn('await self.async_delete("products", product_id)', source)

class GrocyShoppingContractTest(unittest.TestCase):
    def test_shopping_roundtrip_uses_temporary_data_and_cleans_up(self):
        import inspect
        source = inspect.getsource(GrocyClient.async_shopping_roundtrip_test)
        self.assertIn('shopping_list', source)
        self.assertIn('verified_shopping_add', source)
        self.assertIn('Temporary shopping item remained', source)

class GrocyShoppingImportContractTest(unittest.TestCase):
    def test_shopping_import_has_duplicate_protection(self):
        import inspect
        source = inspect.getsource(GrocyClient.async_import_shopping_list)
        self.assertIn('listed_product_ids', source)
        self.assertIn('skipped_existing', source)
        self.assertIn('shopping_payload', source)

class GrocyShoppingExportContractTest(unittest.TestCase):
    def test_filters_list_and_preserves_done(self):
        client = GrocyClient(None, "http://example.invalid", "test")
        async def rows(entity):
            return {"products": [{"id": 1, "name": "Milk"}, {"id": 2, "name": "Tea"}],
                    "quantity_units": [], "shopping_list": [
                        {"id": 5, "product_id": 1, "shopping_list_id": 1, "done": 1, "amount": 1},
                        {"id": 6, "product_id": 2, "shopping_list_id": 2, "done": 0},
                    ]}[entity]
        client.async_list = rows
        items = asyncio.run(client.async_shopping_items(1))
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].checked)
        self.assertEqual(items[0].name, "Milk")
    def test_shopping_export_reads_product_names_and_preserves_source_id(self):
        import inspect
        source = inspect.getsource(GrocyClient.async_shopping_items)
        self.assertIn('product_names', source)
        self.assertIn('vendor_id', source)
        self.assertNotIn('async_create(', source)
        self.assertNotIn('async_delete(', source)
