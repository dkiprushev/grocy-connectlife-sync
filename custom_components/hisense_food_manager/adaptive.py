"""Persistent per-domain retry throttling for optional periodic sync."""
from __future__ import annotations

import asyncio
import logging
import time

_LOGGER = logging.getLogger(__name__)
DELAYS = (60, 120, 300, 600)


class AdaptiveSync:
    """Gate network calls by persisted due time; serialize timer and manual calls."""
    def __init__(self, inventory, shopping, store, clock=time.time):
        self.inventory, self.shopping, self.store, self.clock = inventory, shopping, store, clock
        self.lock = asyncio.Lock()

    async def async_run(self, grocy_list_id: int, shopping_list_name: str) -> dict:
        if grocy_list_id < 1 or not shopping_list_name.strip():
            raise ValueError("Both shopping lists must be selected")
        async with self.lock:
            saved = await self.store.async_load() or {}
            states = dict(saved.get("domains", {}))
            results = {}
            tasks = (
                ("inventory", lambda: self.inventory.async_sync()),
                (f"shopping:{grocy_list_id}:{shopping_list_name.strip()}",
                 lambda: self.shopping.async_sync(grocy_list_id, shopping_list_name.strip())),
            )
            for key, run in tasks:
                state = dict(states.get(key, {}))
                now = self.clock()
                due = float(state.get("next_attempt", 0))
                if now < due:
                    results[key] = {"status": "waiting", "retry_in_seconds": round(due - now)}
                    continue
                result = None
                try:
                    result = await run()
                    if result.get("errors"):
                        raise RuntimeError(f"sync reported errors: {result['errors']}")
                except Exception as err:
                    failures = int(state.get("failures", 0)) + 1
                    delay = DELAYS[min(failures - 1, len(DELAYS) - 1)]
                    if failures == 1:
                        _LOGGER.warning("Automatic %s sync unavailable: %s", key, err)
                    state.update(failures=failures, next_attempt=self.clock() + delay)
                    results[key] = {"status": "error", "error": str(err), "retry_in_seconds": delay,
                                    "result": result}
                else:
                    if state.get("failures", 0):
                        _LOGGER.info("Automatic %s sync recovered", key)
                    conflicts = result.get("conflicts", [])
                    # Only log a changed conflict set, not the same warning each minute.
                    signature = repr(conflicts)
                    if conflicts and signature != state.get("conflicts"):
                        _LOGGER.warning("Automatic %s sync conflicts: %s", key, conflicts)
                    state.update(failures=0, next_attempt=now + DELAYS[0], conflicts=signature)
                    results[key] = {"status": "ok", "result": result, "retry_in_seconds": DELAYS[0]}
                states[key] = state
                await self.store.async_save({**saved, "domains": states})
            return results
