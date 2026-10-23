# Grocy ConnectLife Sync

Custom Home Assistant integration that connects the food-management part of a
ConnectLife refrigerator with [Grocy](https://grocy.info/). It is designed as
one integration with two independent synchronization domains:

1. a shared shopping list;
2. refrigerator inventory.

The integration is intentionally not limited to food names or Grocy product
categories. A **physical Grocy location** decides whether an item belongs in
the refrigerator. For example, face masks stored in `Fridge` should be
synchronized with the refrigerator, while electrical tape stored in `Storage`
must remain Grocy-only.

> The project name is **Grocy ConnectLife Sync**. Its technical Home Assistant
> domain remains `hisense_food_manager` for compatibility with existing config
> entries.

## What is synchronized

### Shopping lists

One explicitly selected Grocy shopping list is paired with one explicitly
selected ConnectLife shopping list.

```text
Grocy shopping list  <-------------------->  ConnectLife shopping list
```

This is a planning flow: an item on a shopping list is not automatically stock
in a refrigerator. It may be food, household goods, or anything else.

### Inventory

Refrigerator inventory is paired only with selected physical locations in
Grocy:

```text
Grocy stock in mapped locations  <-------->  ConnectLife refrigerator inventory
```

The initial conventional mapping is:

| Grocy location | ConnectLife zone | Included in refrigerator inventory sync |
| --- | --- | --- |
| `Fridge` | Refrigerator | Yes |
| `Freezer` | Freezer | Yes |
| `My Fresh Choice` | My Fresh Choice | Yes |
| Any other location, for example `Storage` | — | No — Grocy-only |

The intended production configuration will make the mapped locations explicit
in the integration settings, rather than infer food from a product name.

## Bidirectional synchronization rules

Both domains are intended to be bidirectional:

| Direction | Intended behavior |
| --- | --- |
| ConnectLife shopping list → Grocy | Add or update the corresponding item in the configured Grocy shopping list. |
| Grocy shopping list → ConnectLife | Add or update the corresponding item in the configured ConnectLife shopping list. |
| ConnectLife inventory → Grocy | Reflect refrigerator contents in Grocy stock at the mapped location. |
| Grocy inventory → ConnectLife | Reflect Grocy stock at a mapped refrigerator location in the corresponding ConnectLife zone. |

The safe rollout policy is add/update first. Automatic deletion must remain
disabled until stable item matching, conflict resolution, and an explicit user
decision on deletion semantics are implemented. Synchronization also needs
source identifiers and loop prevention so that an update received from one
system is not echoed back forever.

## Current implementation status

The table separates observed, working API operations from the final
bidirectional product goal. “Not verified” does not mean data was changed; it
means the live round trip has not yet been safely completed.

| Area / operation | Status | Notes |
| --- | --- | --- |
| ConnectLife login and token renewal | Working | Independently authenticated with the owner’s account; credentials are provided through the HA config flow. |
| ConnectLife refrigerator inventory read | Working | Live refrigerator inventory has been returned through the HA service. |
| ConnectLife shopping-list read | Working | Live list metadata/items can be read. |
| ConnectLife catalog and unit read | Working | Available through the current client. |
| Create an empty ConnectLife shopping list | Working | Confirmed in local API testing. |
| Delete a ConnectLife shopping list | Working | Confirmed in local API testing. |
| Rename a ConnectLife list / update a list with `purchases` | Blocked | `fridge/updateShoppingList` returns `control_validate_err` / upstream HTTP 500. The request contract for this account is not yet reproduced. |
| ConnectLife → Grocy shopping-list import | Implemented, not live-verified | Requires a responsive Grocy REST transport in the installed HA instance. |
| Grocy → ConnectLife shopping-list export | Blocked | Depends on the unresolved ConnectLife list-update/write contract. |
| ConnectLife → Grocy inventory import | Implemented, not live-verified | Inventory reads work; end-to-end Grocy writing still needs live verification. |
| Grocy → ConnectLife inventory export | Not implemented | Requires a verified ConnectLife inventory-write contract. |
| Grocy API reachability from Home Assistant | Needs investigation | The Grocy endpoint is reachable from the host, but relevant HA services have previously waited without a response. The client currently needs explicit request timeouts and diagnostics. |
| Full automatic two-way synchronization | Not ready | It must not be presented as working until all four directions above are verified with real data and loop prevention. |

## Data safety and configuration

- The config flow accepts the Grocy URL/API key and ConnectLife account data;
  no change to `secrets.yaml` or `.storage` is required.
- Access and refresh tokens must never be committed, printed in diagnostics, or
  manually copied into source files.
- The integration must never silently delete Grocy stock, ConnectLife inventory,
  or shopping-list entries.
- A write failure must leave the source system unchanged and expose a useful
  diagnostic error instead of guessing an alternative request payload.

## Installation and updates through HACS

This repository is structured as one HACS custom integration. After it is
published on GitHub, install it once in Home Assistant:

1. Open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/dkiprushev/grocy-connectlife-sync` with category
   **Integration**.
3. Find **Grocy ConnectLife Sync** in HACS and choose **Download**.
4. Restart Home Assistant, then configure the integration through
   **Settings → Devices & services**.

Future versions are offered in HACS. Install an offered update there and
restart Home Assistant; manual copying into `custom_components` is no longer
needed. Published GitHub releases are preferred because HACS can show a
version picker; until a release is published, HACS follows the default branch.

## Development

The custom integration lives at:

```text
custom_components/hisense_food_manager/
```

Installing or updating a Home Assistant custom integration requires a Home
Assistant restart. Deployment, configuration-entry migration, and restart are
deliberate operational actions and are not performed by this repository alone.

Credentials, API keys, exported Home Assistant config entries, `.storage`, and
`secrets.yaml` are excluded from version control.

## Next milestones

1. Add timeouts and precise diagnostics to the Grocy client; verify live Grocy
   read/write with a non-destructive fixture.
2. Capture or obtain a documented ConnectLife contract for updating shopping
   lists containing purchases.
3. Implement stable item matching, conflict policy, and loop prevention.
4. Implement the missing Grocy inventory → ConnectLife write direction.
5. Verify each direction independently before enabling scheduled automatic
   synchronization.
