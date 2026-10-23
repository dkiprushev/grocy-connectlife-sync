# Grocy ConnectLife Sync

This is a custom Home Assistant integration that connects a ConnectLife refrigerator with Grocy. It treats the mapped physical location, not a product name or category, as the decision to synchronise: face masks in `Fridge` belong in the refrigerator, while electrical tape in `Storage` stays Grocy-only.

## Current capability

- typed, vendor-neutral models for shopping-list and refrigerator inventory items;
- a deterministic mapping between ConnectLife food zones and Grocy locations;
- a minimal Grocy REST client, using an API key supplied through a config flow instead of `secrets.yaml` or `.storage` edits;
- explicit ConnectLife API route registry and diagnostics contract.

## Deliberately not implemented yet

The ConnectLife Android application contains the routes needed for food manager features, but the exact signed request body for this refrigerator account has not been observed. The client therefore refuses to write rather than guessing a payload. The next milestone is a read-only authenticated contract probe using credentials entered by the owner in the HA config flow; it will not alter ConnectLife or Grocy data.

## Proposed installation diff (not applied)

```diff
+ /config/custom_components/hisense_food_manager/__init__.py
+ /config/custom_components/hisense_food_manager/manifest.json
+ /config/custom_components/hisense_food_manager/config_flow.py
+ /config/custom_components/hisense_food_manager/grocy.py
+ /config/custom_components/hisense_food_manager/connectlife.py
+ /config/custom_components/hisense_food_manager/models.py
```

No YAML, `.storage`, or `secrets.yaml` change is proposed. Installation will require an explicit Home Assistant restart because custom integrations are loaded at startup.

## Intended location mapping

| Grocy location | ConnectLife zone |
| --- | --- |
| `Fridge` | Refrigerator |
| `Freezer` | Freezer |
| `My Fresh Choice` | My Fresh Choice |

All other Grocy locations remain Grocy-only. Credentials, API keys and exported
Home Assistant config entries must never be committed.
