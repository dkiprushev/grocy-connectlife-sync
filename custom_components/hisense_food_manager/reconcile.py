"""Pure three-way reconciliation for explicitly matched records.

Callers persist a baseline only after verifying both systems agree. Missing
records are represented by None; a missing baseline means first contact, not
proof that either side intentionally deleted an item.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class Decision(StrEnum):
    UNCHANGED = "unchanged"
    TO_GROCY = "to_grocy"
    TO_CONNECTLIFE = "to_connectlife"
    CONFLICT = "conflict"
    DELETE_CONFLICT = "delete_conflict"


@dataclass(frozen=True, slots=True)
class Plan(Generic[T]):
    decision: Decision
    value: T | None


def reconcile(grocy: T | None, connectlife: T | None, *,
              baseline: T | None = None, has_baseline: bool = False) -> Plan[T]:
    """Determine direction without guessing the winner of concurrent edits.

    Deletions are deliberately reported for explicit resolution until the
    vendor deletion and stock-consumption semantics are proven end to end.
    """
    if grocy == connectlife:
        return Plan(Decision.UNCHANGED, grocy)
    if not has_baseline:
        if grocy is None:
            return Plan(Decision.TO_GROCY, connectlife)
        if connectlife is None:
            return Plan(Decision.TO_CONNECTLIFE, grocy)
        return Plan(Decision.CONFLICT, None)
    if grocy is None or connectlife is None:
        return Plan(Decision.DELETE_CONFLICT, None)
    if grocy == baseline:
        return Plan(Decision.TO_GROCY, connectlife)
    if connectlife == baseline:
        return Plan(Decision.TO_CONNECTLIFE, grocy)
    return Plan(Decision.CONFLICT, None)
