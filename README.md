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
| Rename a ConnectLife list / update a list with `purchases` | Working locally | Android authentication and signed updates have been verified with empty and populated test lists, followed by readback. |
| ConnectLife → Grocy shopping-list import | Partially verified | HA recognised an existing item; creation of a new item remains unverified. |
| Grocy → ConnectLife shopping-list export | Verified via HA | An item was added and a repeat did not duplicate it. |
| ConnectLife → Grocy inventory import | Verified via HA | A test item was created with its quantity in Fridge. |
| Grocy → ConnectLife inventory export | Verified via HA | Version 0.2.3 exported the test stock and a repeated run added no duplicates. Existing entries are skipped, not updated. |
| Grocy API reachability from Home Assistant | Verified | Snapshot and product, stock, and shopping-list write/cleanup tests passed through HA. |
| Full automatic two-way synchronization | Not ready | It must not be presented as working until all four directions above are verified with real data and loop prevention. |

## Data safety and configuration

### Changes in 0.2.5

- Add an explicitly invoked bidirectional shopping action for one named
  ConnectLife list and one Grocy list ID, with verified readback and stored
  paired-name baseline. Reject missing lists and ambiguous duplicate names.
- Respect Grocy's `done` field and prevent items from different Grocy lists
  from being combined. The default Grocy shopping-list ID is 1.
- Quantities other than 1, checked purchases and one-sided deletions are
  reported as conflicts: the observed ConnectLife purchase DTO cannot encode
  Grocy quantities. Automated scheduled sync and deletion propagation are not
  implemented. Verify the new action in HA before using it with real data.

### Changes in 0.2.4

- Add an explicitly invoked bidirectional inventory action with per-entry
  persisted quantity baselines, a lock, readback verification, and conflict
  reports. Initial differing quantities and concurrent edits are conflicts.
- Support updates to existing ConnectLife inventory, verified against the
  Android endpoint on an isolated test record, and explicit Grocy stock
  reductions with readback. Deletions remain conflicts and are not applied.
- Reject ambiguous shopping-list selection, avoid merging separate lists,
  skip checked purchases, and deduplicate additions.
- Inventory reconciliation matches normalized names and zones, not durable
  cross-system IDs. Automatic scheduled sync and full shopping-list update,
  deletion, and conflict resolution are not yet implemented. Test with
  non-production data before relying on this action.
- Live HA test: changing a test item's quantity from 3 to 4 in Grocy was
  reflected in ConnectLife; changing it from 4 to 2 in ConnectLife was then
  reflected in Grocy. Initial differing quantities produced conflict reports;
  a freezer item reported the missing Grocy Freezer location.

### Changes in 0.2.3

- Read Grocy stock batches from `/api/objects/stock`, preserving the actual
  location and summing multiple batches of a product in the same location.
- Compare summed stock before adding quantities; never silently reduce it.
- Expose the required empty-list name for the ConnectLife shopping write test
  in the Home Assistant action form.
- Add regression coverage for multiple batches and unmapped locations. The
  deployed version exported a test stock item through HA; a repeat added none.

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

1. Verify new-item shopping import in HA.
2. Implement stable item matching, conflict policy, and loop prevention.
3. Verify each direction independently before enabling scheduled automatic
   synchronization.
