"""Grocy ConnectLife Sync integration entry point."""
from __future__ import annotations
from dataclasses import asdict
from typing import TYPE_CHECKING

from .connectlife import ConnectLifeFoodClient
from .auth import FoodManagerAuthSession
from .const import (CONF_GROCY_API_KEY, CONF_GROCY_URL, DOMAIN, SERVICE_PROBE_FOOD_CATALOG,
                    SERVICE_PROBE_INVENTORY, SERVICE_PROBE_SHOPPING_LIST, SERVICE_PROBE_UNITS,
                    SERVICE_GET_SHOPPING_LIST, SERVICE_GET_INVENTORY, SERVICE_GET_CATALOG, SERVICE_GET_UNITS,
                    SERVICE_GET_SHOPPING_LISTS, SERVICE_CREATE_SHOPPING_LIST, SERVICE_DELETE_SHOPPING_LIST,
                    SERVICE_GET_GROCY_SNAPSHOT, SERVICE_TEST_GROCY_ROUNDTRIP, SERVICE_PLAN_INVENTORY_SYNC,
                    SERVICE_TEST_GROCY_STOCK_ROUNDTRIP, SERVICE_IMPORT_INVENTORY_TO_GROCY,
                    SERVICE_TEST_GROCY_SHOPPING_ROUNDTRIP, SERVICE_IMPORT_SHOPPING_TO_GROCY)
from .const import CONF_CONNECTLIFE_PASSWORD, CONF_CONNECTLIFE_USERNAME
from .const import SERVICE_TEST_CONNECTLIFE_SHOPPING_ROUNDTRIP, SERVICE_DELETE_CONNECTLIFE_SHOPPING_TEST
from .const import SERVICE_SYNC_GROCY_SHOPPING_TO_CONNECTLIFE
from .const import (
    SERVICE_SYNC_CONNECTLIFE_INVENTORY_TO_GROCY,
    SERVICE_SYNC_GROCY_INVENTORY_TO_CONNECTLIFE,
    SERVICE_TEST_CONNECTLIFE_INVENTORY_ROUNDTRIP,
    SERVICE_STAGE_GROCY_INVENTORY_ITEM,
    SERVICE_STAGE_CONNECTLIFE_INVENTORY_ITEM,
)
from .grocy import GrocyClient
from .models import FoodZone, InventoryItem
from .coordinator import InventoryCoordinator, ShoppingCoordinator
from .adaptive import AdaptiveSync

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register ConnectLife-only services before config entries are loaded."""
    if not hass.services.has_service(DOMAIN, SERVICE_TEST_CONNECTLIFE_SHOPPING_ROUNDTRIP):
        async def test_connectlife_shopping_roundtrip(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_test_shopping_roundtrip(
                call.data.get("shopping_list_name")
            )
        hass.services.async_register(
            DOMAIN,
            SERVICE_TEST_CONNECTLIFE_SHOPPING_ROUNDTRIP,
            test_connectlife_shopping_roundtrip,
            supports_response="only",
        )
    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_CONNECTLIFE_SHOPPING_TEST):
        async def delete_connectlife_shopping_test(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_delete_shopping_list_for_test(call.data["shopping_list_name"])
        hass.services.async_register(DOMAIN, SERVICE_DELETE_CONNECTLIFE_SHOPPING_TEST, delete_connectlife_shopping_test, supports_response="only")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Create the local Grocy client and register the read-only probe once."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from homeassistant.helpers.storage import Store

    runtime = {
        "grocy": GrocyClient(
            async_get_clientsession(hass), entry.data[CONF_GROCY_URL], entry.data[CONF_GROCY_API_KEY]
        )
    }
    # Credentials live in the config entry. Tokens never do: this independent
    # client keeps them in memory and refreshes them on each API use.
    if entry.data.get(CONF_CONNECTLIFE_USERNAME) and entry.data.get(CONF_CONNECTLIFE_PASSWORD):
        auth = FoodManagerAuthSession(
            entry.data[CONF_CONNECTLIFE_USERNAME], entry.data[CONF_CONNECTLIFE_PASSWORD]
        )
        await auth.async_login()
        runtime["auth"] = auth
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    runtime["inventory_coordinator"] = InventoryCoordinator(
        runtime["grocy"], ConnectLifeFoodClient(hass),
        Store(hass, 1, f"{DOMAIN}_inventory_{entry.entry_id}"),
    )
    runtime["shopping_coordinator"] = ShoppingCoordinator(
        runtime["grocy"], ConnectLifeFoodClient(hass),
        Store(hass, 1, f"{DOMAIN}_shopping_{entry.entry_id}"),
    )
    runtime["adaptive_sync"] = AdaptiveSync(
        runtime["inventory_coordinator"], runtime["shopping_coordinator"],
        Store(hass, 1, f"{DOMAIN}_adaptive_{entry.entry_id}"),
    )

    if not hass.services.has_service(DOMAIN, SERVICE_PROBE_SHOPPING_LIST):
        async def probe_shopping_list(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_probe_shopping_list()
        hass.services.async_register(DOMAIN, SERVICE_PROBE_SHOPPING_LIST, probe_shopping_list,
                                     supports_response="only")
        async def probe_inventory(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_probe_inventory()
        hass.services.async_register(DOMAIN, SERVICE_PROBE_INVENTORY, probe_inventory,
                                     supports_response="only")
        async def probe_food_catalog(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_probe_food_catalog()
        hass.services.async_register(DOMAIN, SERVICE_PROBE_FOOD_CATALOG, probe_food_catalog,
                                     supports_response="only")
        async def probe_units(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_probe_units()
        hass.services.async_register(DOMAIN, SERVICE_PROBE_UNITS, probe_units, supports_response="only")
        async def get_shopping_list(call: ServiceCall) -> dict:
            items = await ConnectLifeFoodClient(hass).async_shopping_items()
            return {"items": [asdict(item) for item in items], "item_count": len(items)}
        hass.services.async_register(DOMAIN, SERVICE_GET_SHOPPING_LIST, get_shopping_list, supports_response="only")
        async def get_shopping_lists(call: ServiceCall) -> dict:
            lists = await ConnectLifeFoodClient(hass).async_shopping_lists()
            return {"lists": lists, "list_count": len(lists)}
        hass.services.async_register(DOMAIN, SERVICE_GET_SHOPPING_LISTS, get_shopping_lists, supports_response="only")
        async def create_shopping_list(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_create_shopping_list(call.data["shopping_list_name"])
        hass.services.async_register(DOMAIN, SERVICE_CREATE_SHOPPING_LIST, create_shopping_list, supports_response="only")
        async def delete_shopping_list(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_delete_shopping_list(call.data["shopping_list_name"])
        hass.services.async_register(DOMAIN, SERVICE_DELETE_SHOPPING_LIST, delete_shopping_list, supports_response="only")
        async def get_inventory(call: ServiceCall) -> dict:
            items = await ConnectLifeFoodClient(hass).async_inventory_items()
            return {"items": [asdict(item) for item in items], "item_count": len(items)}
        hass.services.async_register(DOMAIN, SERVICE_GET_INVENTORY, get_inventory, supports_response="only")
        async def get_catalog(call: ServiceCall) -> dict:
            items = await ConnectLifeFoodClient(hass).async_catalog()
            return {"items": items, "item_count": len(items)}
        hass.services.async_register(DOMAIN, SERVICE_GET_CATALOG, get_catalog, supports_response="only")
        async def get_units(call: ServiceCall) -> dict:
            items = await ConnectLifeFoodClient(hass).async_units()
            return {"items": items, "item_count": len(items)}
        hass.services.async_register(DOMAIN, SERVICE_GET_UNITS, get_units, supports_response="only")
        def grocy_client() -> GrocyClient:
            return hass.data[DOMAIN][entry.entry_id]["grocy"]
        async def sync_inventory_bidirectional(call: ServiceCall) -> dict:
            return await hass.data[DOMAIN][entry.entry_id]["inventory_coordinator"].async_sync()
        hass.services.async_register(DOMAIN, "sync_inventory_bidirectional", sync_inventory_bidirectional, supports_response="only")
        async def sync_shopping_bidirectional(call: ServiceCall) -> dict:
            return await hass.data[DOMAIN][entry.entry_id]["shopping_coordinator"].async_sync(
                int(call.data.get("grocy_shopping_list_id", 1)), str(call.data["shopping_list_name"]))
        hass.services.async_register(DOMAIN, "sync_shopping_bidirectional", sync_shopping_bidirectional, supports_response="only")
        async def sync_adaptive(call: ServiceCall) -> dict:
            return await hass.data[DOMAIN][entry.entry_id]["adaptive_sync"].async_run(
                int(call.data.get("grocy_shopping_list_id", 1)), str(call.data["shopping_list_name"]))
        hass.services.async_register(DOMAIN, "sync_adaptive", sync_adaptive, supports_response="only")
        async def get_grocy_snapshot(call: ServiceCall) -> dict:
            return await grocy_client().async_snapshot()
        hass.services.async_register(DOMAIN, SERVICE_GET_GROCY_SNAPSHOT, get_grocy_snapshot, supports_response="only")
        async def test_grocy_roundtrip(call: ServiceCall) -> dict:
            return await grocy_client().async_roundtrip_test()
        hass.services.async_register(DOMAIN, SERVICE_TEST_GROCY_ROUNDTRIP, test_grocy_roundtrip, supports_response="only")
        async def plan_inventory_sync(call: ServiceCall) -> dict:
            items = await ConnectLifeFoodClient(hass).async_inventory_items()
            return await grocy_client().async_inventory_sync_plan(items)
        hass.services.async_register(DOMAIN, SERVICE_PLAN_INVENTORY_SYNC, plan_inventory_sync, supports_response="only")
        async def test_grocy_stock_roundtrip(call: ServiceCall) -> dict:
            return await grocy_client().async_stock_roundtrip_test()
        hass.services.async_register(DOMAIN, SERVICE_TEST_GROCY_STOCK_ROUNDTRIP, test_grocy_stock_roundtrip, supports_response="only")
        async def import_inventory_to_grocy(call: ServiceCall) -> dict:
            items = await ConnectLifeFoodClient(hass).async_inventory_items()
            return await grocy_client().async_import_inventory(items)
        hass.services.async_register(DOMAIN, SERVICE_IMPORT_INVENTORY_TO_GROCY, import_inventory_to_grocy, supports_response="only")
        async def test_grocy_shopping_roundtrip(call: ServiceCall) -> dict:
            return await grocy_client().async_shopping_roundtrip_test()
        hass.services.async_register(DOMAIN, SERVICE_TEST_GROCY_SHOPPING_ROUNDTRIP, test_grocy_shopping_roundtrip, supports_response="only")
        async def import_shopping_to_grocy(call: ServiceCall) -> dict:
            items = await ConnectLifeFoodClient(hass).async_shopping_items(call.data.get("shopping_list_name"))
            return await grocy_client().async_import_shopping_list(items, int(call.data.get("grocy_shopping_list_id", 1)))
        hass.services.async_register(DOMAIN, SERVICE_IMPORT_SHOPPING_TO_GROCY, import_shopping_to_grocy, supports_response="only")
        async def sync_grocy_shopping_to_connectlife(call: ServiceCall) -> dict:
            items = await grocy_client().async_shopping_items(int(call.data.get("grocy_shopping_list_id", 1)))
            return await ConnectLifeFoodClient(hass).async_sync_grocy_shopping(
                items, call.data.get("shopping_list_name")
            )
        hass.services.async_register(
            DOMAIN, SERVICE_SYNC_GROCY_SHOPPING_TO_CONNECTLIFE,
            sync_grocy_shopping_to_connectlife, supports_response="only",
        )
        async def sync_connectlife_inventory_to_grocy(call: ServiceCall) -> dict:
            items = await ConnectLifeFoodClient(hass).async_inventory_items()
            return await grocy_client().async_sync_inventory_from_connectlife(items)
        hass.services.async_register(
            DOMAIN, SERVICE_SYNC_CONNECTLIFE_INVENTORY_TO_GROCY,
            sync_connectlife_inventory_to_grocy, supports_response="only",
        )
        async def sync_grocy_inventory_to_connectlife(call: ServiceCall) -> dict:
            items = await grocy_client().async_inventory_items()
            return await ConnectLifeFoodClient(hass).async_sync_inventory_from_grocy(items)
        hass.services.async_register(
            DOMAIN, SERVICE_SYNC_GROCY_INVENTORY_TO_CONNECTLIFE,
            sync_grocy_inventory_to_connectlife, supports_response="only",
        )
        async def test_connectlife_inventory_roundtrip(call: ServiceCall) -> dict:
            return await ConnectLifeFoodClient(hass).async_test_inventory_roundtrip()
        hass.services.async_register(
            DOMAIN, SERVICE_TEST_CONNECTLIFE_INVENTORY_ROUNDTRIP,
            test_connectlife_inventory_roundtrip, supports_response="only",
        )
        async def stage_grocy_inventory_item(call: ServiceCall) -> dict:
            item = InventoryItem(
                str(call.data["name"]), FoodZone(str(call.data.get("zone", "fridge"))),
                float(call.data.get("quantity", 1)),
            )
            return await grocy_client().async_stage_inventory_item(item)
        hass.services.async_register(
            DOMAIN, SERVICE_STAGE_GROCY_INVENTORY_ITEM,
            stage_grocy_inventory_item, supports_response="only",
        )
        async def stage_connectlife_inventory_item(call: ServiceCall) -> dict:
            item = InventoryItem(
                str(call.data["name"]), FoodZone(str(call.data.get("zone", "fridge"))),
                float(call.data.get("quantity", 1)),
            )
            return await ConnectLifeFoodClient(hass).async_add_inventory_item(item)
        hass.services.async_register(
            DOMAIN, SERVICE_STAGE_CONNECTLIFE_INVENTORY_ITEM,
            stage_connectlife_inventory_item, supports_response="only",
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_PROBE_SHOPPING_LIST)
        hass.services.async_remove(DOMAIN, SERVICE_PROBE_INVENTORY)
        hass.services.async_remove(DOMAIN, SERVICE_PROBE_FOOD_CATALOG)
        hass.services.async_remove(DOMAIN, SERVICE_PROBE_UNITS)
        hass.services.async_remove(DOMAIN, SERVICE_GET_SHOPPING_LIST)
        hass.services.async_remove(DOMAIN, SERVICE_GET_SHOPPING_LISTS)
        hass.services.async_remove(DOMAIN, SERVICE_CREATE_SHOPPING_LIST)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_SHOPPING_LIST)
        hass.services.async_remove(DOMAIN, SERVICE_GET_INVENTORY)
        hass.services.async_remove(DOMAIN, SERVICE_GET_CATALOG)
        hass.services.async_remove(DOMAIN, SERVICE_GET_UNITS)
        hass.services.async_remove(DOMAIN, SERVICE_GET_GROCY_SNAPSHOT)
        hass.services.async_remove(DOMAIN, SERVICE_TEST_GROCY_ROUNDTRIP)
        hass.services.async_remove(DOMAIN, SERVICE_PLAN_INVENTORY_SYNC)
        hass.services.async_remove(DOMAIN, SERVICE_TEST_GROCY_STOCK_ROUNDTRIP)
        hass.services.async_remove(DOMAIN, SERVICE_IMPORT_INVENTORY_TO_GROCY)
        hass.services.async_remove(DOMAIN, SERVICE_TEST_GROCY_SHOPPING_ROUNDTRIP)
        hass.services.async_remove(DOMAIN, SERVICE_IMPORT_SHOPPING_TO_GROCY)
        hass.services.async_remove(DOMAIN, SERVICE_TEST_CONNECTLIFE_SHOPPING_ROUNDTRIP)
        hass.services.async_remove(DOMAIN, SERVICE_SYNC_GROCY_SHOPPING_TO_CONNECTLIFE)
        hass.services.async_remove(DOMAIN, SERVICE_SYNC_CONNECTLIFE_INVENTORY_TO_GROCY)
        hass.services.async_remove(DOMAIN, SERVICE_SYNC_GROCY_INVENTORY_TO_CONNECTLIFE)
        hass.services.async_remove(DOMAIN, SERVICE_TEST_CONNECTLIFE_INVENTORY_ROUNDTRIP)
        hass.services.async_remove(DOMAIN, SERVICE_STAGE_GROCY_INVENTORY_ITEM)
        hass.services.async_remove(DOMAIN, SERVICE_STAGE_CONNECTLIFE_INVENTORY_ITEM)
        hass.services.async_remove(DOMAIN, "sync_inventory_bidirectional")
        hass.services.async_remove(DOMAIN, "sync_shopping_bidirectional")
        hass.services.async_remove(DOMAIN, "sync_adaptive")
    return True
