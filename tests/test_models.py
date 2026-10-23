import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))
from hisense_food_manager.models import FoodZone, InventoryItem, ShoppingItem, normalized_name
from hisense_food_manager.grocy import GrocyClient

class ModelsTest(unittest.TestCase):
    def test_name_normalization_is_stable(self):
        self.assertEqual(normalized_name("  Молоко   3,2% "), "молоко 3,2%")

    def test_zone_mapping_preserves_three_fridge_compartments(self):
        self.assertEqual(GrocyClient.location_name(FoodZone.FRIDGE), "Fridge")
        self.assertEqual(GrocyClient.location_name(FoodZone.FREEZER), "Freezer")
        self.assertEqual(GrocyClient.location_name(FoodZone.MY_FRESH_CHOICE), "My Fresh Choice")

    def test_grocy_payloads_keep_quantity_and_expiry(self):
        stock = GrocyClient.inventory_payload(InventoryItem("Молоко", FoodZone.FRIDGE, 2, "шт", "2026-10-01"), 4, 2)
        todo = GrocyClient.shopping_payload(ShoppingItem("Молоко", 2, "шт"), 4)
        self.assertEqual(stock, {"product_id": 4, "location_id": 2, "amount": 2, "best_before_date": "2026-10-01"})
        self.assertEqual(todo, {"product_id": 4, "amount": 2, "note": "шт"})

if __name__ == "__main__":
    unittest.main()
