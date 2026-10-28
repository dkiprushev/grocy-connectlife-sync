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

Synchronization uses the last verified state and source identifiers to detect
which side changed. An unambiguous deletion of a previously paired item is
propagated; concurrent edits or changed identifiers are reported as conflicts.
Initial matches still depend on normalized names and zones. Existing differences
must be resolved explicitly; the integration does not choose a winner for them.

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
| ConnectLife → Grocy shopping-list import | Verified via HA | A new test item was imported in 0.2.5; quantity and completion updates were verified in 0.2.9. |
| Grocy → ConnectLife shopping-list export | Verified via HA | An item was added and a repeat did not duplicate it. |
| ConnectLife → Grocy inventory import | Verified via HA | A test item was created with its quantity in Fridge. |
| Grocy → ConnectLife inventory export | Verified via HA | Creation, quantity updates and repeated runs without duplicates have been verified. |
| Grocy API reachability from Home Assistant | Verified | Snapshot and product, stock, and shopping-list write/cleanup tests passed through HA. |
| Paired inventory deletions | Verified via HA | Tested in both directions on 2026-10-28; stock in an unmapped Storage location survived removal from Fridge. |
| Paired shopping deletions | Verified via HA | Both directions verified, including ConnectLife → Grocy through the adaptive action on 2026-10-28. |
| Automatic synchronization | Enabled for supervised use | All four exchange directions have live coverage. The first scheduled HA run on 2026-10-28 returned `ok` in both domains. Retry/backoff is tested with injected failures; a real cloud outage and long-term scheduled use remain unverified. |

## Data safety and configuration

### Verification on 2026-10-28

- Created separate test inventory items in Grocy and ConnectLife, then paired
  both through the HA action. Removing the Grocy stock removed its ConnectLife
  counterpart; deleting the other item in ConnectLife consumed its Fridge stock
  in Grocy. Both action responses reported the exact deleted keys and no errors.
- Five units of the same product in the unmapped `Storage` location remained;
  product records were not deleted. The two test Fridge stocks were removed.
- `Freezer` was found and the existing freezer pizza was imported into Grocy.
  Nine initial inventory quantity conflicts remained unchanged (Grocy 2,
  ConnectLife 1); the shopping Cola conflict also remains unresolved.
- Fault tests cover persistent independent retries, repeated ten-minute retry
  ceiling, recovery to one minute and no stock/baseline writes after a cloud
  read timeout. These are controlled local tests, not an actual HA cloud outage.
- The HA adaptive action returned `ok` for both domains; it propagated deletion
  of the ConnectLife shopping test fixture into Grocy. An immediate repeat
  returned `waiting` for both domains without another exchange.
- The blueprint source follows `main` so future re-imports can fetch updates.
- Re-imported the blueprint through the HA interface and created the automation
  for Grocy list 1 and ConnectLife `codex integration verified`. HA trace for
  2026-10-28 11:20 Europe/Kiev confirms the minute trigger and `status: ok` for
  both inventory and shopping, with no repeated additions or deletions.
  The automation is enabled; initial quantity conflicts remain reported.

### Changes in 0.2.9

- The ConnectLife `foodNum` and `status` fields were verified on a temporary
  live list; shopping quantities and completion states can now be reconciled.
- Previously verified paired identifiers allow one-sided deletions when the
  remaining item is unchanged. Concurrent edits and changed IDs are conflicts.
  Grocy stock removal applies only to the mapped location, not the product or
  other locations. Inventory deletion in both directions was subsequently
  verified through HA on 2026-10-28.
- Previous name-only and quantity-only baselines are migrated conservatively:
  they cannot authorize deletion until both sides have been matched again.
- Refresh the icon and add action icons for Home Assistant.

### Changes in 0.2.8

- The optional automatic sync now uses independent persistent retry schedules
  for inventory and shopping: after consecutive errors wait 1, 2, 5, then 10
  minutes; after success return to a one-minute interval. Conflicts do not
  count as connectivity failures. First failure and recovery are logged;
  repeated identical conflicts are logged only when they change.
- A response from ConnectLife does not prove the refrigerator is online. The
  available `offline_state` value has not been validated for this appliance,
  so this version does not infer connectivity from it.

### Changes in 0.2.7

- Change the optional periodic automation interval from 15 minutes to one
  minute for more timely shopping-list updates. Polling latency and vendor
  limits are not guaranteed; overlapping scheduled runs are skipped.

### Changes in 0.2.6

- Add an optional Home Assistant automation blueprint. It must be
  imported and enabled explicitly, and reports conflicts and errors to the
  Home Assistant log. Installing the integration does not activate it.

### Changes in 0.2.5

- Add an explicitly invoked bidirectional shopping action for one named
  ConnectLife list and one Grocy list ID, with verified readback and stored
  paired-name baseline. Reject missing lists and ambiguous duplicate names.
- Respect Grocy's `done` field and prevent items from different Grocy lists
  from being combined. The default Grocy shopping-list ID is 1.
- Quantities other than 1, checked purchases and one-sided deletions are
  reported as conflicts: the observed ConnectLife purchase DTO cannot encode
  Grocy quantities. Automated scheduled sync and deletion propagation are not
  implemented. In a live HA test, one isolated item was added in each direction;
  a repeat added neither again, while a quantity of 2 was reported as a conflict.

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
- Propagated deletions are reported in the action response; ambiguous or
  concurrently modified records are not deleted.
- A write failure must leave the source system unchanged and expose a useful
  diagnostic error instead of guessing an alternative request payload.

## Installation and updates through HACS

### Optional automatic sync

Home Assistant can run the two bidirectional actions using an automation.
The optional blueprint at
`blueprints/automation/grocy_connectlife_sync/periodic_sync.yaml` runs every
minute, uses `mode: single` to avoid overlapping executions, and logs
conflicts and errors. Import it into **Settings → Automations & scenes →
Blueprints**, create an automation, and select the exact ConnectLife list
name and Grocy list ID. Creating and enabling that automation authorizes
periodic writes in both directions; it is not enabled by installing the
integration. A lightweight tick runs every minute, but on failed exchanges
network attempts back off independently by 1, 2, 5, then 10 minutes. After a
successful exchange that direction returns to a one-minute interval. State is
persisted over HA restarts; slow requests and skipped overlapping runs can add
delay. Vendor rate limits and long-term reliability are not yet verified.

The blueprint does not resolve conflicts; confirmed paired deletions are
propagated. Review existing conflicts and mapped locations before enabling it.
Initial matching uses normalized names. Start with supervised use and review
the response/logs; a real cloud outage and long-term scheduled use remain
unverified. Cloud API availability is sufficient: refrigerator power/online
state does not gate synchronization.

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

1. Resolve the initial quantity conflicts by explicitly selecting the intended
   values in Grocy or ConnectLife; automatic conflict resolution is not provided.
2. Observe scheduled use, recovery after an actual cloud outage and vendor rate
   limits. A malformed or silently incomplete successful cloud snapshot remains
   a limitation of the unofficial API.
