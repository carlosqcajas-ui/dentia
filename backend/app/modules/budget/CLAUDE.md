# Budget module

Dental treatment quotes, versioning, signatures, PDF.

## Public API

Routes mounted at `/api/v1/budget/`. Staff-authenticated only — there
is no patient-facing/unauthenticated subset (see ADR 0019, which
removed the public link this module used to expose):

- CRUD + version + signature workflow (legacy).
- `POST /budgets/{id}/{accept,reject,renegotiate,accept-in-clinic,resend}`
  — acceptance/rejection is always staff-recorded, never patient
  self-service.
- `GET  /budgets/{id}/pdf` — unsigned PDF.
- `GET  /budgets/{id}/pdf/signed` — signed PDF (404 if not signed).
- `GET  /budgets/{id}/signature` — signature metadata (no raw PNG).
- `PUT  /budgets/{id}/total` — set the total on an `is_manual_total`
  budget by hand. No status guard (unlike `PUT /budgets/{id}`).

## Dependencies

`manifest.depends = ["patients", "catalog", "odontogram"]`.

## Permissions

`budget.read`, `budget.write`, `budget.admin`,
`budget.renegotiate`, `budget.accept_in_clinic`.

## Tools exposed

Agent tools in `tools.py` (wrap `BudgetService` / `BudgetWorkflowService`).

| Tool | Category | Wraps | Permission |
|---|---|---|---|
| `list_budgets` | READ | `BudgetService.list_budgets` | `budget.read` |
| `get_budget` | READ | `BudgetService.get_budget` | `budget.read` |
| `send_budget` | DESTRUCTIVE | `BudgetWorkflowService.send_budget` | `budget.write` |

`send_budget` is DESTRUCTIVE because emailing the patient is an
irreversible external side effect. Amounts here are the budget axis
only — never combined with payments data.

## Events emitted

- `budget.sent`
- `budget.accepted` (snapshot payload includes `accepted_via`,
  `plan_id`).
- `budget.rejected` (snapshot payload with `rejection_reason`,
  `plan_id`).
- `budget.expired` (snapshot payload with `days_overdue`, `plan_id`).
- `budget.renegotiated` (snapshot payload with `plan_id`).

## Events consumed

- `treatment_plan.treatment_added` / `treatment_plan.treatment_removed`
  / `treatment_plan.budget_sync_requested` — sync with treatment_plan
  via **snapshot payloads only** (no cross-module ORM imports).
- `odontogram.treatment.performed` — mark line items done when the
  underlying tooth treatment is performed.

## Frontend slots exposed

| Slot | Ctx | Consumer |
|---|---|---|
| `budget.detail.sidebar` | `{ budget }` | `payments` registers `BudgetPaymentsCard` (cobrado vs pendiente, "Cobrar" action). Other modules may add follow-up reminders, signature blocks, etc. |

Budget never imports its slot consumers — the registry is the only
contract.

## Lifecycle

- `removable=False`. Billing depends on accepted budgets.

## Gotchas

- **Budget → treatment_plan is event-driven, never direct.** Don't
  import treatment_plan services or models from here. The reverse
  direction (treatment_plan → budget) is allowed because budget is in
  treatment_plan's depends. See ADR 0003.
- **Snapshot-only event handlers.** `_on_treatment_added_to_plan` and
  friends consume the data carried in the payload (catalog_item_id,
  tooth, surfaces, unit_price, budget_id) — no fetches against the
  publisher's tables.
- **Plan reverse-lookup uses raw SQL** (`_lookup_plan_id`) instead of
  importing the `TreatmentPlan` model, so event payloads can carry
  `plan_id` without violating ADR 0003.
- **Plan-derived budgets follow the clinic's manual-total policy.**
  `clinic.settings['budget_manual_total_default']` decides whether
  `create_from_plan_snapshot` (called on plan confirm) builds an
  itemized budget or a manual-total one seeded with the plan's sum.
  The `generate-budget` endpoint on treatment_plan can override it
  per call. Both paths must stay in sync — a clinic that flips the
  setting expects it to apply everywhere a plan becomes a budget.
- **The three plan→budget event handlers bail out on
  `is_manual_total`.** Don't remove those guards: a manual-total
  budget has no items by definition, and mirroring plan items into it
  leaves orphan rows that no total reflects.
- **Budget versioning** keeps every prior version — never overwrite.
- **No patient self-service.** There is deliberately no unauthenticated
  route that lets a patient accept/reject/view a budget — see ADR
  0019. Don't reintroduce one without a new ADR.
- **Signed PDF tamper-evidence.** On accept, the workflow renders
  the signed PDF and stores its SHA-256 on
  ``BudgetSignature.document_hash``. The same hash is shown to
  staff and is what binds the signature to that exact PDF. Don't
  bypass this on new acceptance paths.
- **Manual-total budgets (`is_manual_total=True`)** skip the item
  system entirely — no `BudgetItem` rows, `total`/`subtotal` set
  directly via `BudgetService.set_manual_total` instead of
  `_recalculate_totals`. `set_manual_total` has **no status guard**,
  unlike every other budget mutation — it can be called even on an
  `accepted` budget. Doing so does **not** regenerate the signed PDF's
  `document_hash`; the edit is only visible via a `BudgetHistory`
  `total_updated` entry, and the frontend flags it by comparing
  `Budget.updated_at` against the signature's `signed_at`. This is a
  deliberate, scoped exception to tamper-evidence — see ADR 0018. Don't
  extend "editable after acceptance" to any other field without a new
  ADR.
- **`billing`/`reports.get_by_treatment`/`notifications`' itemized
  email/`migration_import`'s Gesdén mapper all assume `BudgetItem`
  rows exist.** They're never invoked in a way that requires them for
  `is_manual_total` budgets (empty `items` is already a valid state
  those modules already had to tolerate for draft itemized budgets),
  but don't add new cross-module code that assumes every budget has
  items.
- ``budget.completed`` no longer exists. The transition
  ``accepted → completed`` and the manual "Mark completed" button
  were removed in 2026-04: ``completed`` was a bookkeeping flag
  with no auto-trigger and no real consumer. Use invoice paid /
  fully invoiced as the financial-closure signal instead.

## Related ADRs

- `docs/adr/0001-modular-plugin-architecture.md`
- `docs/adr/0003-event-bus-over-direct-imports.md`
- `docs/adr/0018-budget-manual-total.md`
- `docs/adr/0019-remove-budget-patient-self-service.md` (supersedes
  `0006-budget-public-link-2-factor-auth.md`)

## CHANGELOG

See `./CHANGELOG.md`.
