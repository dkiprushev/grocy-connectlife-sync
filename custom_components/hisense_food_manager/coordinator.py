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
            results = {"to_grocy": [], "to_connectlife": [], "unchanged": [], "conflicts": [], "errors": []}
            for key in sorted(set(left) | set(right) | set(baseline)):
                if len(left.get(key, [])) > 1 or len(right.get(key, [])) > 1:
                    results["conflicts"].append({"key": key, "reason": "ambiguous_match"})
                    continue
                a = left[key][0] if left.get(key) else None
                b = right[key][0] if right.get(key) else None
                decision = reconcile(a.quantity if a else None, b.quantity if b else None,
                                     baseline=baseline.get(key), has_baseline=key in baseline)
                if decision.decision in (Decision.CONFLICT, Decision.DELETE_CONFLICT):
                    results["conflicts"].append({"key": key, "reason": decision.decision.value,
                                                 "grocy": a.quantity if a else None,
                                                 "connectlife": b.quantity if b else None,
                                                 "baseline": baseline.get(key)})
                    continue
                if decision.decision == Decision.UNCHANGED:
                    if a is None:
                        baseline.pop(key, None)
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
                    baseline[key] = check_a[0].quantity
                    await self.store.async_save({**stored, "inventory": baseline})
                    results[direction].append(key)
                except Exception as err:
                    results["errors"].append({"key": key, "error": str(err)})
            await self.store.async_save({**stored, "inventory": baseline})
            return results


class ShoppingCoordinator:
    """Exchange missing active names within one explicitly selected list pair.

    ConnectLife's observed purchase DTO has no quantity; non-unit quantities,
    checked items, deletions, and ambiguous names are reported, not guessed.
    """
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
            baseline = set(pairs.get(pair_key, []))
            a, b = await asyncio.gather(self.grocy.async_shopping_items(grocy_list_id),
                                        self.connectlife.async_shopping_items(name))
            left, right = defaultdict(list), defaultdict(list)
            for item in a: left[normalized_name(item.name)].append(item)
            for item in b: right[normalized_name(item.name)].append(item)
            result = {"to_grocy": [], "to_connectlife": [], "unchanged": [], "conflicts": [], "errors": []}
            for key in sorted(set(left) | set(right) | baseline):
                aa, bb = left.get(key, []), right.get(key, [])
                if not key or len(aa) > 1 or len(bb) > 1:
                    result["conflicts"].append({"key": key, "reason": "ambiguous_match"})
                    continue
                x, y = (aa[0] if aa else None), (bb[0] if bb else None)
                if (x and x.checked) or (y and y.checked):
                    result["conflicts"].append({"key": key, "reason": "checked_status_not_supported"})
                    continue
                if x and x.quantity != 1:
                    result["conflicts"].append({"key": key, "reason": "quantity_not_supported"})
                    continue
                if key in baseline and (not x or not y):
                    if not x and not y:
                        baseline.discard(key)
                    else:
                        result["conflicts"].append({"key": key, "reason": "delete_conflict"})
                    continue
                if x and y:
                    baseline.add(key)
                    result["unchanged"].append(key)
                    continue
                try:
                    if x:
                        await self.connectlife.async_sync_grocy_shopping([x], name)
                        direction = "to_connectlife"
                    else:
                        await self.grocy.async_import_shopping_list([y], grocy_list_id)
                        direction = "to_grocy"
                    after_a, after_b = await asyncio.gather(self.grocy.async_shopping_items(grocy_list_id),
                                                            self.connectlife.async_shopping_items(name))
                    if sum(normalized_name(i.name) == key and not i.checked for i in after_a) != 1 or sum(
                            normalized_name(i.name) == key and not i.checked for i in after_b) != 1:
                        raise RuntimeError("Readback did not confirm unique active items")
                    baseline.add(key)
                    pairs[pair_key] = sorted(baseline)
                    await self.store.async_save({**stored, "pairs": pairs})
                    result[direction].append(key)
                except Exception as err:
                    result["errors"].append({"key": key, "error": str(err)})
            pairs[pair_key] = sorted(baseline)
            await self.store.async_save({**stored, "pairs": pairs})
            return result
