"""Conflict-aware, persisted inventory exchange between Grocy and ConnectLife."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from .models import InventoryItem, normalized_name
from .reconcile import Decision, reconcile


class InventoryCoordinator:
    """Exchange one snapshot at a time; only confirmed pairs become a baseline."""
    def __init__(self, grocy, connectlife, store):
        self.grocy = grocy
        self.connectlife = connectlife
        self.store = store
        self.lock = asyncio.Lock()

    @staticmethod
    def key(item: InventoryItem) -> str:
        return f"{item.zone.value}:{normalized_name(item.name)}"

    @classmethod
    def index(cls, items):
        indexed = defaultdict(list)
        for item in items:
            indexed[cls.key(item)].append(item)
        return indexed

    async def async_sync(self):
        async with self.lock:
            stored = await self.store.async_load() or {}
            baseline = dict(stored.get("inventory", {}))
            grocy_items, connectlife_items = await asyncio.gather(
                self.grocy.async_inventory_items(), self.connectlife.async_inventory_items())
            left, right = self.index(grocy_items), self.index(connectlife_items)
            results = {"to_grocy": [], "to_connectlife": [], "unchanged": [], "deleted": [], "conflicts": [], "errors": []}
            for key in sorted(set(left) | set(right) | set(baseline)):
                if len(left.get(key, [])) > 1 or len(right.get(key, [])) > 1:
                    results["conflicts"].append({"key": key, "reason": "ambiguous_match"})
                    continue
                a = left[key][0] if left.get(key) else None
                b = right[key][0] if right.get(key) else None
                old = baseline.get(key)
                if isinstance(old, dict):
                    if ((a and a.vendor_id != old.get("grocy_id")) or
                            (b and b.vendor_id != old.get("connectlife_id"))):
                        results["conflicts"].append({"key": key, "reason": "identifier_changed"})
                        continue
                    if (a is None) != (b is None):
                        remaining = a or b
                        expected_id = old.get("grocy_id" if a else "connectlife_id")
                        if not expected_id or remaining.vendor_id != expected_id or remaining.quantity != old.get("quantity"):
                            results["conflicts"].append({"key": key, "reason": "delete_conflict"})
                            continue
                        try:
                            if a:
                                if self.index(await self.connectlife.async_inventory_items()).get(key):
                                    raise RuntimeError("ConnectLife item reappeared before deletion")
                                await self.grocy.async_delete_inventory_item(a)
                            else:
                                if self.index(await self.grocy.async_inventory_items()).get(key):
                                    raise RuntimeError("Grocy item reappeared before deletion")
                                fresh = self.index(await self.connectlife.async_inventory_items()).get(key, [])
                                if len(fresh) != 1 or fresh[0].vendor_id != expected_id or fresh[0].quantity != remaining.quantity:
                                    raise RuntimeError("ConnectLife item changed before deletion")
                                await self.connectlife.async_delete_inventory_item(expected_id)
                            baseline.pop(key, None)
                            await self.store.async_save({**stored, "inventory": baseline})
                            results["deleted"].append(key)
                        except Exception as err:
                            results["errors"].append({"key": key, "error": str(err)})
                            break  # Do not continue a batch after uncertain read/write state.
                        continue
                old_quantity = old.get("quantity") if isinstance(old, dict) else old
                decision = reconcile(a.quantity if a else None, b.quantity if b else None,
                                     baseline=old_quantity, has_baseline=key in baseline)
                if decision.decision in (Decision.CONFLICT, Decision.DELETE_CONFLICT):
                    results["conflicts"].append({"key": key, "reason": decision.decision.value,
                                                 "grocy": a.quantity if a else None,
                                                 "connectlife": b.quantity if b else None,
                                                 "baseline": old_quantity})
                    continue
                if decision.decision == Decision.UNCHANGED:
                    if a is None:
                        baseline.pop(key, None)
                    else:
                        if a.vendor_id and b.vendor_id:
                            baseline[key] = {"quantity": a.quantity, "grocy_id": a.vendor_id, "connectlife_id": b.vendor_id}
                        else:
                            baseline[key] = a.quantity
                    results["unchanged"].append(key)
                    continue
                try:
                    if decision.decision == Decision.TO_GROCY:
                        outcome = await self.grocy.async_sync_inventory_from_connectlife([b], allow_reductions=True)
                        if outcome.get("skipped_unmapped"):
                            raise RuntimeError("Grocy location is not mapped")
                        direction = "to_grocy"
                    else:
                        outcome = await self.connectlife.async_sync_inventory_from_grocy([a])
                        if outcome.get("conflicts"):
                            raise RuntimeError("ConnectLife match is ambiguous")
                        direction = "to_connectlife"
                    current_a, current_b = await asyncio.gather(
                        self.grocy.async_inventory_items(), self.connectlife.async_inventory_items())
                    check_a, check_b = self.index(current_a).get(key, []), self.index(current_b).get(key, [])
                    if len(check_a) != 1 or len(check_b) != 1 or check_a[0].quantity != check_b[0].quantity:
                        raise RuntimeError("Readback did not confirm matching quantities")
                    baseline[key] = ({"quantity": check_a[0].quantity, "grocy_id": check_a[0].vendor_id,
                                      "connectlife_id": check_b[0].vendor_id}
                                     if check_a[0].vendor_id and check_b[0].vendor_id else check_a[0].quantity)
                    await self.store.async_save({**stored, "inventory": baseline})
                    results[direction].append(key)
                except Exception as err:
                    results["errors"].append({"key": key, "error": str(err)})
                    break
            await self.store.async_save({**stored, "inventory": baseline})
            return results


class ShoppingCoordinator:
    """Reconcile quantity, checked state and known-ID deletions for one list pair."""
    def __init__(self, grocy, connectlife, store):
        self.grocy, self.connectlife, self.store = grocy, connectlife, store
        self.lock = asyncio.Lock()

    async def async_sync(self, grocy_list_id: int, connectlife_list_name: str):
        if grocy_list_id < 1 or not connectlife_list_name.strip():
            raise ValueError("Both shopping lists must be selected")
        name = connectlife_list_name.strip()
        async with self.lock:
            grocy_lists = await self.grocy.async_list("shopping_lists")
            if not any(int(row.get("id", -1)) == grocy_list_id for row in grocy_lists):
                raise ValueError("Selected Grocy shopping list does not exist")
            stored = await self.store.async_load() or {}
            pairs = dict(stored.get("pairs", {}))
            pair_key = f"{grocy_list_id}:{name}"
            prior = pairs.get(pair_key, {})
            # v0.2.5 stored only names; those are not sufficient authority to
            # propagate deletion or select a winner on the first upgraded run.
            baseline = ({key: {"legacy": True} for key in prior} if isinstance(prior, list)
                        else dict(prior) if isinstance(prior, dict) else {})
            a, b = await asyncio.gather(self.grocy.async_shopping_items(grocy_list_id),
                                        self.connectlife.async_shopping_items(name))
            left, right = defaultdict(list), defaultdict(list)
            for item in a: left[normalized_name(item.name)].append(item)
            for item in b: right[normalized_name(item.name)].append(item)
            result = {"to_grocy": [], "to_connectlife": [], "unchanged": [], "deleted": [], "conflicts": [], "errors": []}
            for key in sorted(set(left) | set(right) | set(baseline)):
                aa, bb = left.get(key, []), right.get(key, [])
                if not key or len(aa) > 1 or len(bb) > 1:
                    result["conflicts"].append({"key": key, "reason": "ambiguous_match"})
                    continue
                x, y = (aa[0] if aa else None), (bb[0] if bb else None)
                old = baseline.get(key)
                if old and old.get("legacy"):
                    if not x or not y:
                        result["conflicts"].append({"key": key, "reason": "legacy_delete_conflict"})
                        continue
                    old = None
                def value(item):
                    return {"quantity": item.quantity, "checked": item.checked} if item else None
                ax, by = value(x), value(y)
                if old and ((x and x.vendor_id != old.get("grocy_id")) or
                            (y and y.vendor_id != old.get("connectlife_id"))):
                    result["conflicts"].append({"key": key, "reason": "identifier_changed"})
                    continue
                if old and (not x or not y):
                    if not x and not y:
                        baseline.pop(key, None)
                        result["unchanged"].append(key)
                        continue
                    remaining = y if not x else x
                    expected_id = old.get("connectlife_id" if not x else "grocy_id")
                    if not expected_id or remaining.vendor_id != expected_id or value(remaining) != old.get("value"):
                        result["conflicts"].append({"key": key, "reason": "delete_conflict"})
                        continue
                    try:
                        if not x:
                            if any(normalized_name(i.name) == key for i in await self.grocy.async_shopping_items(grocy_list_id)):
                                raise RuntimeError("Grocy purchase reappeared before deletion")
                            await self.connectlife.async_change_shopping_item(name, expected_id, delete=True)
                        else:
                            if any(normalized_name(i.name) == key for i in await self.connectlife.async_shopping_items(name)):
                                raise RuntimeError("ConnectLife purchase reappeared before deletion")
                            await self.grocy.async_change_shopping_item(grocy_list_id, expected_id, delete=True)
                        baseline.pop(key, None)
                        pairs[pair_key] = baseline
                        await self.store.async_save({**stored, "pairs": pairs})
                        result["deleted"].append(key)
                    except Exception as err:
                        result["errors"].append({"key": key, "error": str(err)})
                        break
                    continue
                decision = reconcile(ax, by, baseline=old.get("value") if old else None, has_baseline=bool(old))
                if decision.decision == Decision.CONFLICT:
                    result["conflicts"].append({"key": key, "reason": "conflict", "grocy": ax, "connectlife": by})
                    continue
                if decision.decision == Decision.UNCHANGED:
                    if x and y and x.vendor_id and y.vendor_id:
                        baseline[key] = {"value": ax, "grocy_id": x.vendor_id, "connectlife_id": y.vendor_id}
                    result["unchanged"].append(key)
                    continue
                try:
                    if decision.decision == Decision.TO_GROCY:
                        if x:
                            if not x.vendor_id: raise RuntimeError("Grocy item ID is missing")
                            await self.grocy.async_change_shopping_item(grocy_list_id, x.vendor_id, quantity=y.quantity, checked=y.checked)
                        else:
                            if y.checked: raise RuntimeError("Cannot create a checked purchase automatically")
                            await self.grocy.async_import_shopping_list([y], grocy_list_id)
                        direction = "to_grocy"
                    else:
                        if y:
                            if not y.vendor_id: raise RuntimeError("ConnectLife item ID is missing")
                            await self.connectlife.async_change_shopping_item(name, y.vendor_id, quantity=x.quantity, checked=x.checked)
                        else:
                            if x.checked: raise RuntimeError("Cannot create a checked purchase automatically")
                            await self.connectlife.async_sync_grocy_shopping([x], name)
                        direction = "to_connectlife"
                    after_a, after_b = await asyncio.gather(self.grocy.async_shopping_items(grocy_list_id),
                                                            self.connectlife.async_shopping_items(name))
                    check_a = [i for i in after_a if normalized_name(i.name) == key]
                    check_b = [i for i in after_b if normalized_name(i.name) == key]
                    if len(check_a) != 1 or len(check_b) != 1 or value(check_a[0]) != value(check_b[0]) or not check_a[0].vendor_id or not check_b[0].vendor_id:
                        raise RuntimeError("Readback did not confirm unique matching items with IDs")
                    baseline[key] = {"value": value(check_a[0]), "grocy_id": check_a[0].vendor_id, "connectlife_id": check_b[0].vendor_id}
                    pairs[pair_key] = baseline
                    await self.store.async_save({**stored, "pairs": pairs})
                    result[direction].append(key)
                except Exception as err:
                    result["errors"].append({"key": key, "error": str(err)})
                    break
            pairs[pair_key] = baseline
            await self.store.async_save({**stored, "pairs": pairs})
            return result
