import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))
from hisense_food_manager.connectlife import ConnectLifeFoodClient


class ConnectLifeSchemaTest(unittest.TestCase):
    def test_schema_redacts_values_and_keeps_nested_keys(self):
        schema = ConnectLifeFoodClient._schema({
            "roomName": "private room name",
            "fridgeFoods": [{"foodName": "private food name", "foodUnitId": 3}],
        })
        self.assertEqual(schema["roomName"], "str")
        self.assertEqual(schema["fridgeFoods"][0]["foodName"], "str")
        self.assertNotIn("private food name", str(schema))


if __name__ == "__main__":
    unittest.main()
