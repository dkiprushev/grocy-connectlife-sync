"""Fault tests use actual clients/coordinators and never a real transport."""
import asyncio
import copy
import unittest
from unittest.mock import AsyncMock

from hisense_food_manager.grocy import GrocyClient, GrocyError
from hisense_food_manager.connectlife import ConnectLifeFoodClient, ApiContractUnavailable
from hisense_food_manager.coordinator import InventoryCoordinator, ShoppingCoordinator
from hisense_food_manager.models import InventoryItem, ShoppingItem, FoodZone
from test_coordinator import Store


class SnapshotSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_nullable_android_purchase_compatibility(self):
        row = {'purchaseId': 17, 'cusFoodName': 'Milk', 'status': 0, 'foodNum': None}
        item = ConnectLifeFoodClient._shopping_from_row(row)
        self.assertEqual((item.name, item.quantity, item.vendor_id), ('Milk', 1, '17'))
        for malformed in ({'cusFoodName': 'Milk', 'foodNum': None},
                          {'purchaseId': 17, 'cusFoodName': 'Milk'},
                          {**row, 'foodNum': 'bad'}, {**row, 'foodNum': float('nan')}):
            with self.subTest(row=malformed), self.assertRaises(ApiContractUnavailable):
                ConnectLifeFoodClient._shopping_from_row(malformed)
        self.assertEqual(ConnectLifeFoodClient._shopping_from_row({**row, 'foodNum': 0}).quantity, 0)

    async def test_malformed_grocy_collections_are_not_empty(self):
        for payload in ({}, None, {'error': 'schema'}, 'bad', [None], [{}]):
            with self.subTest(payload=payload):
                client = GrocyClient(None, 'http://never.invalid', 'synthetic')
                client._request = AsyncMock(return_value=payload)
                with self.assertRaises(GrocyError):
                    await client.async_list('shopping_list')

    async def test_true_empty_grocy_collection_remains_valid(self):
        client = GrocyClient(None, 'http://never.invalid', 'synthetic')
        client._request = AsyncMock(return_value=[])
        self.assertEqual(await client.async_list('shopping_list'), [])

    async def test_bad_quantity_is_not_defaulted(self):
        for value in (None, 'bad', float('nan'), float('inf'), -1, True):
            with self.subTest(value=value):
                with self.assertRaises(GrocyError):
                    GrocyClient._quantity(value)
                with self.assertRaises(ApiContractUnavailable):
                    ConnectLifeFoodClient._number(value)

    async def test_malformed_cloud_inventory_fails_instead_of_empty(self):
        client = ConnectLifeFoodClient(None)
        for payload in ({}, None, {'error': 'bad'}, {'fridgeRooms': None},
                        {'fridgeRooms': [{'fridgeFoods': [None]}]},
                        {'fridgeRooms': [{'fridgeFoods': [{'foodNum': 2}]}]}):
            with self.subTest(payload=payload):
                client._async_get = AsyncMock(return_value=payload)
                with self.assertRaises(ApiContractUnavailable):
                    await client.async_inventory_items()

    async def test_true_empty_cloud_room_and_list_remain_valid(self):
        client = ConnectLifeFoodClient(None)
        client._async_get = AsyncMock(return_value={'fridgeRooms': [{'roomCode': 'freezer', 'fridgeFoods': []}]})
        self.assertEqual(await client.async_inventory_items(), [])
        client._async_get = AsyncMock(return_value={'shoppingLists': [{'id': 1, 'name': 'Test', 'purchaseDataList': []}]})
        self.assertEqual(await client.async_shopping_items('Test'), [])

    async def test_cloud_shopping_missing_collection_or_row_is_rejected(self):
        client = ConnectLifeFoodClient(None)
        for row in ({'id': 1, 'name': 'Test'},
                    {'id': 1, 'name': 'Test', 'purchaseDataList': None},
                    {'id': 1, 'name': 'Test', 'purchaseDataList': [None]},
                    {'id': 1, 'name': 'Test', 'purchaseDataList': [{'foodNum': 1}]}):
            with self.subTest(row=row):
                client._async_get = AsyncMock(return_value={'shoppingLists': [row]})
                with self.assertRaises(ApiContractUnavailable):
                    await client.async_shopping_items('Test')

    async def test_bad_grocy_read_never_deletes_remote_or_saves_baseline(self):
        for domain in ('inventory', 'shopping'):
            with self.subTest(domain=domain):
                client = GrocyClient(None, 'http://never.invalid', 'synthetic')
                client._request = AsyncMock(return_value={})
                remote = AsyncMock()
                remote.async_inventory_items.return_value = [InventoryItem('Milk', FoodZone.FRIDGE, 2, vendor_id='remote')]
                remote.async_shopping_items.return_value = [ShoppingItem('Milk', 2, vendor_id='remote')]
                store = Store()
                store.data = {'inventory': {'fridge:milk': {'quantity': 2, 'grocy_id': '1:2', 'connectlife_id': 'remote'}},
                              'pairs': {'1:Test': {'milk': {'value': {'quantity': 2, 'checked': False}, 'grocy_id': '1', 'connectlife_id': 'remote'}}}}
                before = copy.deepcopy(store.data)
                with self.assertRaises(GrocyError):
                    if domain == 'inventory':
                        await InventoryCoordinator(client, remote, store).async_sync()
                    else:
                        await ShoppingCoordinator(client, remote, store).async_sync(1, 'Test')
                self.assertEqual(store.data, before)
                remote.async_delete_inventory_item.assert_not_called()
                remote.async_change_shopping_item.assert_not_called()
                remote.async_sync_inventory_from_grocy.assert_not_called()

    async def test_partial_product_catalog_cannot_hide_stock(self):
        client = GrocyClient(None, 'http://never.invalid', 'synthetic')
        async def request(method, path):
            return {'products': [], 'locations': [{'id': 2, 'name': 'Fridge'}],
                    'stock': [{'product_id': 1, 'location_id': 2, 'amount': 1}],
                    'quantity_units': []}[path.rsplit('/', 1)[-1]]
        client._request = request
        with self.assertRaises(GrocyError):
            await client.async_inventory_items()

    async def test_bad_cloud_read_never_changes_grocy_or_baseline(self):
        remote = ConnectLifeFoodClient(None)
        remote._async_get = AsyncMock(return_value={})
        local = AsyncMock()
        local.async_list.return_value = [{'id': 1}]
        local.async_inventory_items.return_value = [InventoryItem('Milk', FoodZone.FRIDGE, 2, vendor_id='local')]
        local.async_shopping_items.return_value = [ShoppingItem('Milk', 2, vendor_id='local')]
        store = Store()
        store.data = {'inventory': {'fridge:milk': 2}, 'pairs': {'1:Test': {'milk': {'value': {'quantity': 2, 'checked': False}, 'grocy_id': 'local', 'connectlife_id': 'remote'}}}}
        before = copy.deepcopy(store.data)
        with self.assertRaises(ApiContractUnavailable):
            await InventoryCoordinator(local, remote, store).async_sync()
        with self.assertRaises(ApiContractUnavailable):
            await ShoppingCoordinator(local, remote, store).async_sync(1, 'Test')
        self.assertEqual(store.data, before)
        local.async_delete_inventory_item.assert_not_called()
        local.async_change_shopping_item.assert_not_called()
        local.async_sync_inventory_from_connectlife.assert_not_called()

    async def test_committed_update_with_lost_response_does_not_duplicate_on_retry(self):
        from test_coordinator import Side
        class LostResponse(Side):
            async def async_sync_inventory_from_connectlife(self, items, **kwargs):
                self.quantity = items[0].quantity
                self.writes += 1
                raise TimeoutError('response lost after commit')
        local, remote, store = LostResponse(2), Side(2), Store()
        sync = InventoryCoordinator(local, remote, store)
        await sync.async_sync()
        remote.quantity = 3
        self.assertEqual(len((await sync.async_sync())['errors']), 1)
        self.assertEqual(store.data['inventory']['fridge:milk'], 2)
        self.assertEqual((await sync.async_sync())['unchanged'], ['fridge:milk'])
        self.assertEqual(store.data['inventory']['fridge:milk'], 3)
        self.assertEqual(local.writes, 1)

    async def test_failure_stops_remaining_inventory_batch(self):
        local, remote, store = AsyncMock(), AsyncMock(), Store()
        local.async_inventory_items.return_value = []
        remote.async_inventory_items.return_value = [InventoryItem('Milk', FoodZone.FRIDGE, 1), InventoryItem('Tea', FoodZone.FRIDGE, 1)]
        local.async_sync_inventory_from_connectlife.side_effect = TimeoutError('uncertain commit')
        result = await InventoryCoordinator(local, remote, store).async_sync()
        self.assertEqual(len(result['errors']), 1)
        local.async_sync_inventory_from_connectlife.assert_awaited_once()
        self.assertEqual(store.data['inventory'], {})

    async def test_failure_stops_remaining_shopping_batch(self):
        local, remote, store = AsyncMock(), AsyncMock(), Store()
        local.async_list.return_value = [{'id': 1}]
        local.async_shopping_items.return_value = []
        remote.async_shopping_items.return_value = [ShoppingItem('Milk', 1), ShoppingItem('Tea', 1)]
        local.async_import_shopping_list.side_effect = TimeoutError('uncertain commit')
        result = await ShoppingCoordinator(local, remote, store).async_sync(1, 'Test')
        self.assertEqual(len(result['errors']), 1)
        local.async_import_shopping_list.assert_awaited_once()
        self.assertEqual(store.data['pairs']['1:Test'], {})
