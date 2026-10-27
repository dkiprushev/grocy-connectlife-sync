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
