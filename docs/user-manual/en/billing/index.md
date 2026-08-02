---
module: billing
last_verified_commit: 0eb12fe
---

# Billing

> [!WARNING]
> **This section is hidden in the UI.** This clinic does not issue
> formal invoices — budgets and payments carry the accounting trail.
> The invoice pages remain reachable by URL but no longer appear in the
> navigation or in Settings. Dentia integrates with no tax authority:
> invoices are internal documents, not fiscal receipts.

The billing module manages the clinic's invoices, credit notes, and
their PDFs. It also owns the configuration of invoice series.

Invoices can be created from scratch or from an accepted budget on
the `budget` module. **Payments** link to invoices through
`invoice_payments`, but payments live in the `payments` module
(`billing` depends on `payments`, not the other way around).

## Screens

- [Invoice list](./screens/invoices.md) — search, filter, and open
  invoices.
- [Invoice detail](./screens/invoices_id.md) — view the invoice,
  PDF, linked payments, issue, send, void.
- [Edit draft](./screens/invoices_id_edit.md) — only `draft`
  invoices.
- [New invoice](./screens/invoices_new.md) — create a free invoice
  (without a budget).
- [Invoice from budget](./screens/invoices_from-budget_budgetId.md)
  — invoice all or some items from an accepted budget.
- [Invoice series (settings)](./screens/settings_invoice-series.md)
  — manage prefixes, counters, and the active series.

## Quick reference

| Action | Required permission |
|--------|---------------------|
| View invoices, download PDFs | `billing.read` |
| Create drafts, edit, issue, send email | `billing.write` |
| Void an issued invoice or delete a series | `billing.admin` |
| Edit invoice series | `billing.admin` |

## Related modules

- **Budget** — main source of lines: an invoice can be generated
  from an accepted budget.
- **Payments** — `billing` depends on `payments`; an invoice links
  to one or more payments via `invoice_payments`.
- **Catalog** — provider of invoiceable items and VAT types.
- **Reports** — billing KPIs and trends live under
  `/reports/billing`.
