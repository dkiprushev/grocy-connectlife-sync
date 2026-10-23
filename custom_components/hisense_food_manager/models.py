"""Data models and stable mappings independent of either upstream API."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum

class FoodZone(StrEnum):
    FRIDGE = "fridge"
    FREEZER = "freezer"
    MY_FRESH_CHOICE = "my_fresh_choice"

ZONE_LABELS = {
    FoodZone.FRIDGE: "Холодильник",
    FoodZone.FREEZER: "Морозильник",
    FoodZone.MY_FRESH_CHOICE: "My Fresh Choice",
}

@dataclass(frozen=True, slots=True)
class ShoppingItem:
    name: str
    quantity: float = 1
    unit: str | None = None
    checked: bool = False
    vendor_id: str | None = None

@dataclass(frozen=True, slots=True)
class InventoryItem:
    name: str
    zone: FoodZone
    quantity: float = 1
    unit: str | None = None
    best_before: str | None = None
    vendor_id: str | None = None

def normalized_name(value: str) -> str:
    """Key used only for matching; original display text is preserved."""
    return " ".join(value.casefold().split())
