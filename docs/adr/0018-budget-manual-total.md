# 0018 — Budgets support an optional "manual total" mode

- **Status:** accepted
- **Date:** 2026-07-21
- **Deciders:** Core team
- **Tags:** modules, budget, billing

## Context

`Budget.total` was a fully derived field: `BudgetService._recalculate_totals`
(`backend/app/modules/budget/service.py`) recomputes it from `BudgetItem`
rows on every item add/update/remove, and there was no way for staff to
set it by hand.

Clinics that don't use the `billing` module's fiscal invoicing (no
Veri*Factu, no per-item invoicing — e.g. deployments outside Spain that
only need quotes + payments/receipts) don't need a line-item breakdown
on their budgets. They need a quote with a total they can type directly
and correct at any time, including after the patient has already signed
it in `accepted` status.

`billing` invoices budgets **per item** (`InvoiceItem.budget_item_id`,
partial invoicing tracked via `BudgetItem.invoiced_quantity`,
`InvoiceService.create_from_budget`). `reports.BudgetReportService.get_by_treatment`
aggregates by `BudgetItem.catalog_item_id`. `notifications` lists items
in the "budget sent" email. `migration_import` maps legacy `budget_line`
rows to `BudgetItem` for clinics migrating from Gesdén. Dentia is
open-source, multi-clinic software (BSL 1.1) — removing `BudgetItem`
to satisfy one deployment would break `billing`/`reports`/`notifications`/
`migration_import` for every other installation that does use itemized
quotes and fiscal invoicing.

`payments` was checked and requires no change: `PaymentAllocation.target_type`
is already `'budget'` (the whole budget) or `'on_account'`, never
per-item — receipts were already amount-free against the budget total.

## Decision

Add `Budget.is_manual_total: bool` (default `False`). When set:

- `total` (and `subtotal`, mirrored; `total_discount`/`total_tax` zeroed)
  is set directly via `BudgetService.set_manual_total`, never touched by
  `_recalculate_totals` (which now no-ops for these budgets).
- The budget has no `BudgetItem` rows — `add_item`/`remove_item`/item
  update all reject manual-total budgets.
- `set_manual_total` has **no status guard** — it can be called in any
  non-deleted status, including `accepted`. This is the one deliberate
  exception to "budgets are only editable in draft."
- `send_budget`/`accept_budget` accept a manual-total budget with
  `total > 0` and no items (instead of requiring `items` to be
  non-empty).
- Editing the total after acceptance does **not** regenerate the signed
  PDF or `BudgetSignature.document_hash` — the hash keeps reflecting the
  total at signing time. The change is only visible via a
  `BudgetHistory` entry (`action="total_updated"`). The UI surfaces a
  "total modificado después de la firma" warning by comparing the
  budget's `updated_at` against the signature's `signed_at`.

Itemized budgets (`is_manual_total=False`, the default) are completely
unaffected — same model, same validation, same tamper-evidence
guarantee as before.

## Consequences

### Good

- Delivers exactly what deployments without fiscal invoicing need
  (quote + free-amount receipt) with a small, additive change.
- Zero risk to `billing`, `reports`, `notifications`, `migration_import`
  — they never see `is_manual_total=True` unless a clinic opts in, and
  their code paths (which all assume `BudgetItem` rows) are untouched.
- Itemized budgets keep their full tamper-evidence guarantee.

### Bad / accepted trade-offs

- Manual-total budgets, once `accepted`, no longer have a document that
  provably wasn't altered after signing — the signed PDF's hash can go
  stale relative to the current total. This is intentional (the
  business need is "always editable") but it means manual-total budgets
  cannot be relied on as tamper-evident legal records the way itemized
  budgets are.
- Two budget "shapes" now exist in one table (itemized vs. manual). Any
  future budget feature must consider which mode it applies to.

## Alternatives considered

- **Remove `BudgetItem` entirely, make every budget manual-total** —
  rejected: breaks `billing`'s per-item partial invoicing, `reports`'
  treatment breakdown, `notifications`' itemized email, and
  `migration_import`'s Gesdén importer for every clinic that uses them,
  not just the one that doesn't need them.
- **Keep total derived, add a manual "adjustment" delta field** —
  rejected: doesn't satisfy "monto libre, sin ítems" (still requires
  creating at least one item to have a base to adjust from).
- **Regenerate the signed PDF/hash on every post-acceptance total
  edit** — rejected: would misrepresent the hash as still proving the
  original signed amount, when the whole point of this feature is that
  the total changed after signing. Better to be explicit (history entry
  + UI warning) than to fake continuity.

## How to verify the rule still holds

- `backend/tests` budget suite: manual-total create/send/accept without
  items, `set_manual_total` before and after `accepted`, itemized
  budgets unaffected.
- `_recalculate_totals` early-returns on `is_manual_total` — grep for
  any new call site that bypasses `BudgetService` and writes `Budget.total`
  directly without going through `set_manual_total`.

## References

- `backend/app/modules/budget/service.py` — `set_manual_total`, `_recalculate_totals`
- `backend/app/modules/budget/workflow.py` — `accept_budget`, `send_budget`
- `backend/app/modules/budget/CLAUDE.md`
- `docs/adr/0006-budget-public-link-2-factor-auth.md`
