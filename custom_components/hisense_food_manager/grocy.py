"""Small async client for the documented local Grocy REST API."""
from __future__ import annotations
from urllib.parse import urljoin
from typing import TYPE_CHECKING
import asyncio

if TYPE_CHECKING:
    import aiohttp
from .models import FoodZone, InventoryItem, ShoppingItem, normalized_name

class GrocyError(RuntimeError):
    pass

class GrocyClient:
    def __init__(self, session: "aiohttp.ClientSession", base_url: str, api_key: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/") + "/"
        self._headers = {"GROCY-API-KEY": api_key, "Accept": "application/json"}

    def _url(self, path: str) -> str:
        return urljoin(self._base_url, path.lstrip("/"))

    async def async_system_info(self) -> dict:
        return await self._request("GET", "/api/system/info")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with asyncio.timeout(15):
                async with self._session.request(method, self._url(path), headers=self._headers, **kwargs) as response:
                    if response.status >= 400:
                        detail = (await response.text())[:300]
                        raise GrocyError(f"Grocy returned HTTP {response.status}: {detail}")
                    if response.status == 204:
                        return {}
                    return await response.json()
        except TimeoutError as err:
            raise GrocyError(f"Grocy request timed out after 15 seconds: {method} {path}") from err

    @staticmethod
    def inventory_payload(item: InventoryItem, product_id: int, location_id: int) -> dict:
        return {"product_id": product_id, "location_id": location_id, "amount": item.quantity,
                "best_before_date": item.best_before}

    @staticmethod
    def shopping_payload(item: ShoppingItem, product_id: int) -> dict:
        return {"product_id": product_id, "amount": item.quantity, "note": item.unit or ""}

    @staticmethod
    def location_name(zone: FoodZone) -> str:
        return {FoodZone.FRIDGE: "Fridge", FoodZone.FREEZER: "Freezer",
                FoodZone.MY_FRESH_CHOICE: "My Fresh Choice"}[zone]

    async def async_list(self, entity: str) -> list[dict]:
        data = await self._request("GET", f"/api/objects/{entity}")
        return data if isinstance(data, list) else []

    async def async_create(self, entity: str, payload: dict) -> int:
        data = await self._request("POST", f"/api/objects/{entity}", json=payload)
        return int(data["created_object_id"])

    async def async_delete(self, entity: str, object_id: int) -> None:
        await self._request("DELETE", f"/api/objects/{entity}/{object_id}")

    async def async_import_shopping_list(self, items: list[ShoppingItem]) -> dict:
        """Import missing ConnectLife shopping items into Grocy without duplicates."""
        products, locations, units, shopping = await __import__("asyncio").gather(
            self.async_list("products"), self.async_list("locations"),
            self.async_list("quantity_units"), self.async_list("shopping_list")
        )
        if not units or not locations:
            raise GrocyError("Grocy needs a quantity unit and location")
        product_ids = {normalized_name(str(row.get("name", ""))): int(row["id"])
                       for row in products if row.get("name") and row.get("id") is not None}
        listed_product_ids = {int(row["product_id"]) for row in shopping if row.get("product_id") is not None}
        location_id = int(locations[0]["id"])
        created: list[str] = []
        skipped: list[str] = []
        for item in items:
            key = normalized_name(item.name)
            product_id = product_ids.get(key)
            if product_id is None:
                product_id = await self.async_create("products", {
                    "name": item.name, "location_id": location_id,
                    "qu_id_purchase": units[0]["id"], "qu_id_stock": units[0]["id"],
                    "min_stock_amount": 0,
                })
                product_ids[key] = product_id
            if product_id in listed_product_ids:
                skipped.append(item.name)
                continue
            await self.async_create("shopping_list", self.shopping_payload(item, product_id))
            listed_product_ids.add(product_id)
            created.append(item.name)
        return {"created": created, "created_count": len(created),
                "skipped_existing": skipped, "skipped_count": len(skipped)}

    async def async_shopping_items(self) -> list[ShoppingItem]:
        """Read the current Grocy shopping list without changing it.

        Grocy stores the product name separately from the shopping-list row.
        Keeping the Grocy row ID as ``vendor_id`` makes the source observable
        in service responses without exposing any credentials.
        """
        products, units, shopping = await __import__("asyncio").gather(
            self.async_list("products"), self.async_list("quantity_units"),
            self.async_list("shopping_list"),
        )
        product_names = {
            int(row["id"]): str(row["name"])
            for row in products
            if row.get("id") is not None and row.get("name")
        }
        unit_names = {
            int(row["id"]): str(row["name"])
            for row in units
            if row.get("id") is not None and row.get("name")
        }
        items: list[ShoppingItem] = []
        for row in shopping:
            product_id = row.get("product_id")
            if product_id is None or int(product_id) not in product_names:
                continue
            amount = row.get("amount", 1)
            try:
                quantity = float(amount)
            except (TypeError, ValueError):
                quantity = 1
            items.append(ShoppingItem(
                name=product_names[int(product_id)],
                quantity=quantity,
                unit=(str(row.get("note")) if row.get("note") else unit_names.get(row.get("qu_id"))),
                checked=bool(row.get("done_date")),
                vendor_id=str(row.get("id")) if row.get("id") is not None else None,
            ))
        return items

    async def async_import_inventory(self, items: list[InventoryItem]) -> dict:
        """Import missing fridge products and one stock entry for each into Grocy.

        Existing product names are never modified and never receive duplicate stock.
        This is intentionally an initial import, not an automatic recurring sync.
        """
        products, locations, units = await __import__("asyncio").gather(
            self.async_list("products"), self.async_list("locations"), self.async_list("quantity_units")
        )
        if not units:
            raise GrocyError("Grocy has no quantity units")
        existing = {normalized_name(str(row.get("name", ""))): int(row["id"])
                    for row in products if row.get("name") and row.get("id") is not None}
        location_ids = {str(row.get("name", "")): int(row["id"])
                        for row in locations if row.get("id") is not None}
        created: list[dict] = []
        skipped: list[str] = []
        for item in items:
            key = normalized_name(item.name)
            if key in existing:
                skipped.append(item.name)
                continue
            location_name = self.location_name(item.zone)
            location_id = location_ids.get(location_name)
            if location_id is None:
                raise GrocyError(f"Grocy location is missing: {location_name}")
            product_id = await self.async_create("products", {
                "name": item.name, "location_id": location_id,
                "qu_id_purchase": units[0]["id"], "qu_id_stock": units[0]["id"],
                "min_stock_amount": 0,
            })
            try:
                await self._request("POST", f"/api/stock/products/{product_id}/add", json={
                    "amount": item.quantity, "location_id": location_id,
                })
            except Exception:
                await self.async_delete("products", product_id)
                raise
            existing[key] = product_id
            created.append({"name": item.name, "product_id": product_id, "quantity": item.quantity,
                            "location": location_name})
        return {"created": created, "created_count": len(created),
                "skipped_existing": skipped, "skipped_count": len(skipped)}

    async def async_inventory_items(self) -> list[InventoryItem]:
        """Read stock that belongs to one of the mapped refrigerator locations.

        Grocy products can exist in several locations, therefore stock rows are
        the source of truth for the location and quantity, not a product's
        default location. Rows outside the three mapped locations are never
        exposed to ConnectLife.
        """
        products, locations, stock, units = await asyncio.gather(
            self.async_list("products"), self.async_list("locations"),
            self._request("GET", "/api/stock"), self.async_list("quantity_units"),
        )
        product_by_id = {
            int(row["id"]): row for row in products
            if row.get("id") is not None and row.get("name")
        }
        unit_by_id = {
            int(row["id"]): str(row["name"]) for row in units
            if row.get("id") is not None and row.get("name")
        }
        zone_by_location_id = {
            int(row["id"]): zone
            for zone in FoodZone
            for row in locations
            if row.get("id") is not None and row.get("name") == self.location_name(zone)
        }
        items: list[InventoryItem] = []
        for row in stock if isinstance(stock, list) else []:
            product_id = row.get("product_id")
            location_id = row.get("location_id")
            if product_id is None or location_id is None:
                continue
            product = product_by_id.get(int(product_id))
            zone = zone_by_location_id.get(int(location_id))
            if product is None or zone is None:
                continue
            try:
                quantity = float(row.get("amount", 0))
            except (TypeError, ValueError):
                continue
            if quantity <= 0:
                continue
            items.append(InventoryItem(
                name=str(product["name"]), zone=zone, quantity=quantity,
                unit=unit_by_id.get(product.get("qu_id_stock")),
                best_before=row.get("best_before_date") or None,
                vendor_id=f"{product_id}:{location_id}",
            ))
        return items

    async def async_sync_inventory_from_connectlife(self, items: list[InventoryItem]) -> dict:
        """Add missing mapped Grocy stock from a ConnectLife inventory snapshot.

        This only touches the three mapped refrigerator locations. Products in
        every other Grocy location stay untouched. Reductions are reported and
        never applied without an explicit deletion/conflict policy.
        """
        products, locations, units, stock = await asyncio.gather(
            self.async_list("products"), self.async_list("locations"),
            self.async_list("quantity_units"), self._request("GET", "/api/stock"),
        )
        if not units:
            raise GrocyError("Grocy has no quantity units")
        location_by_name = {
            str(row.get("name")): int(row["id"]) for row in locations if row.get("id") is not None
        }
        product_by_name = {
            normalized_name(str(row.get("name", ""))): int(row["id"])
            for row in products if row.get("id") is not None and row.get("name")
        }
        amounts = {
            (int(row["product_id"]), int(row["location_id"])): float(row.get("amount") or 0)
            for row in stock if isinstance(row, dict)
            and row.get("product_id") is not None and row.get("location_id") is not None
        }
        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        skipped_reductions: list[str] = []
        skipped_unmapped: list[dict[str, str]] = []
        for item in items:
            location_name = self.location_name(item.zone)
            location_id = location_by_name.get(location_name)
            if location_id is None:
                # A refrigerator can expose zones which the household has not
                # chosen to model in Grocy yet.  Do not turn one unmapped zone
                # into a failure for all of the other mapped zones.
                skipped_unmapped.append({"name": item.name, "location": location_name})
                continue
            key = normalized_name(item.name)
            product_id = product_by_name.get(key)
            if product_id is None:
                product_id = await self.async_create("products", {
                    "name": item.name, "location_id": location_id,
                    "qu_id_purchase": units[0]["id"], "qu_id_stock": units[0]["id"],
                    "min_stock_amount": 0,
                })
                product_by_name[key] = product_id
                created.append(item.name)
            current = amounts.get((product_id, location_id), 0)
            if current == item.quantity:
                unchanged.append(item.name)
                continue
            if item.quantity > current:
                await self._request("POST", f"/api/stock/products/{product_id}/add", json={
                    "amount": item.quantity - current, "location_id": location_id,
                })
            else:
                skipped_reductions.append(item.name)
                continue
            amounts[(product_id, location_id)] = item.quantity
            updated.append(item.name)
        return {
            "created": created, "created_count": len(created),
            "updated": updated, "updated_count": len(updated),
            "unchanged": unchanged, "unchanged_count": len(unchanged),
            "skipped_reductions": skipped_reductions, "skipped_reductions_count": len(skipped_reductions),
            "skipped_unmapped": skipped_unmapped,
            "skipped_unmapped_count": len(skipped_unmapped),
        }

    async def async_stage_inventory_item(self, item: InventoryItem) -> dict:
        """Create or set one explicitly named Grocy stock item for a live E2E run."""
        units, locations, products = await asyncio.gather(
            self.async_list("quantity_units"), self.async_list("locations"), self.async_list("products")
        )
        if not units:
            raise GrocyError("Grocy has no quantity units")
        location_name = self.location_name(item.zone)
        location = next((row for row in locations if row.get("name") == location_name), None)
        if location is None or location.get("id") is None:
            raise GrocyError(f"Grocy location is missing: {location_name}")
        key = normalized_name(item.name)
        product = next((row for row in products if normalized_name(str(row.get("name", ""))) == key), None)
        created = product is None
        if product is None:
            product_id = await self.async_create("products", {
                "name": item.name, "location_id": location["id"],
                "qu_id_purchase": units[0]["id"], "qu_id_stock": units[0]["id"],
                "min_stock_amount": 0,
            })
        else:
            product_id = int(product["id"])
        await self._request("POST", f"/api/stock/products/{product_id}/add", json={
            "amount": item.quantity, "location_id": location["id"],
        })
        stock = await self._request("GET", "/api/stock")
        if not any(
            int(row.get("product_id", -1)) == product_id
            and int(row.get("location_id", -1)) == int(location["id"])
            and float(row.get("amount") or 0) == item.quantity
            for row in stock if isinstance(row, dict)
        ):
            raise GrocyError(f"Grocy did not return staged stock: {item.name}")
        return {"name": item.name, "zone": item.zone.value, "quantity": item.quantity,
                "product_id": product_id, "created_product": created, "verified": True}

    async def async_inventory_sync_plan(self, items: list[InventoryItem]) -> dict:
        """Describe the Grocy work needed for fridge inventory without writing."""
        products, locations = await __import__("asyncio").gather(
            self.async_list("products"), self.async_list("locations")
        )
        existing = {
            normalized_name(str(row.get("name", ""))): int(row["id"])
            for row in products if row.get("name") and row.get("id") is not None
        }
        location_ids = {str(row.get("name", "")): int(row["id"]) for row in locations if row.get("id") is not None}
        actions: list[dict] = []
        for item in items:
            key = normalized_name(item.name)
            product_id = existing.get(key)
            actions.append({
                "operation": "add_stock" if product_id else "create_product_and_add_stock",
                "name": item.name, "existing_product_id": product_id,
                "location": self.location_name(item.zone),
                "location_available": self.location_name(item.zone) in location_ids,
                "quantity": item.quantity, "unit": item.unit, "best_before": item.best_before,
                "connectlife_id": item.vendor_id,
            })
        return {"source_item_count": len(items), "planned_actions": actions,
                "existing_product_count": len(products), "available_locations": sorted(location_ids)}

    async def async_snapshot(self) -> dict:
        """Return only counts and local location names, without changing Grocy."""
        products, locations, units, shopping = await __import__("asyncio").gather(
            self.async_list("products"), self.async_list("locations"),
            self.async_list("quantity_units"), self.async_list("shopping_list"),
        )
        return {
            "product_count": len(products), "shopping_item_count": len(shopping),
            "locations": [str(row.get("name", "")) for row in locations],
            "unit_count": len(units),
        }

    async def async_shopping_roundtrip_test(self) -> dict:
        """Verify Grocy shopping-list creation and cleanup with a temporary product."""
        units, locations = await __import__("asyncio").gather(
            self.async_list("quantity_units"), self.async_list("locations")
        )
        if not units or not locations:
            raise GrocyError("Grocy needs at least one quantity unit and one location")
        product_id = await self.async_create("products", {
            "name": "[HA test] Grocy ConnectLife Sync shopping",
            "location_id": locations[0]["id"],
            "qu_id_purchase": units[0]["id"], "qu_id_stock": units[0]["id"],
            "min_stock_amount": 0,
        })
        shopping_id: int | None = None
        try:
            shopping_id = await self.async_create("shopping_list", {
                "product_id": product_id, "amount": 1, "note": "[HA test]",
            })
            shopping = await self.async_list("shopping_list")
            if not any(int(row.get("id", -1)) == shopping_id for row in shopping):
                raise GrocyError("Grocy did not return its test shopping item")
        finally:
            if shopping_id is not None:
                await self.async_delete("shopping_list", shopping_id)
            await self.async_delete("products", product_id)
        shopping_after = await self.async_list("shopping_list")
        if shopping_id is not None and any(int(row.get("id", -1)) == shopping_id for row in shopping_after):
            raise GrocyError("Temporary shopping item remained after deletion")
        return {"created_product_id": product_id, "verified_shopping_add": True, "verified_deleted": True}

    async def async_stock_roundtrip_test(self) -> dict:
        """Verify the Grocy stock endpoint using one temporary labelled product."""
        units, locations = await __import__("asyncio").gather(
            self.async_list("quantity_units"), self.async_list("locations")
        )
        if not units or not locations:
            raise GrocyError("Grocy needs at least one quantity unit and one location")
        product_id = await self.async_create("products", {
            "name": "[HA test] Grocy ConnectLife Sync stock",
            "location_id": locations[0]["id"],
            "qu_id_purchase": units[0]["id"], "qu_id_stock": units[0]["id"],
            "min_stock_amount": 0,
        })
        try:
            await self._request("POST", f"/api/stock/products/{product_id}/add", json={
                "amount": 1, "location_id": locations[0]["id"],
            })
            stock = await self._request("GET", "/api/stock")
            if not any(int(row.get("product_id", -1)) == product_id for row in stock if isinstance(row, dict)):
                raise GrocyError("Grocy did not return stock for its test product")
        finally:
            await self.async_delete("products", product_id)
        products_after = await self.async_list("products")
        stock_after = await self._request("GET", "/api/stock")
        if any(int(row.get("id", -1)) == product_id for row in products_after):
            raise GrocyError("Temporary stock product remained after deletion")
        if any(int(row.get("product_id", -1)) == product_id for row in stock_after if isinstance(row, dict)):
            raise GrocyError("Temporary stock remained after product deletion")
        return {"created_product_id": product_id, "verified_stock_add": True, "verified_deleted": True}

    async def async_roundtrip_test(self) -> dict:
        """Create, verify, and remove one clearly-labelled temporary Grocy product."""
        units = await self.async_list("quantity_units")
        locations = await self.async_list("locations")
        if not units or not locations:
            raise GrocyError("Grocy needs at least one quantity unit and one location")
        product_id = await self.async_create("products", {
            "name": "[HA test] Grocy ConnectLife Sync",
            "location_id": locations[0]["id"],
            "qu_id_purchase": units[0]["id"], "qu_id_stock": units[0]["id"],
            "min_stock_amount": 0,
        })
        try:
            after_create = await self.async_list("products")
            if not any(int(row.get("id", -1)) == product_id for row in after_create):
                raise GrocyError("Grocy did not return its newly created test product")
        finally:
            await self.async_delete("products", product_id)
        after_delete = await self.async_list("products")
        if any(int(row.get("id", -1)) == product_id for row in after_delete):
            raise GrocyError("Grocy test product remained after deletion")
        return {"created_product_id": product_id, "verified_created": True, "verified_deleted": True}
