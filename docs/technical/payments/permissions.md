---
module: payments
last_verified_commit: e8b7c5c
---

# Payments — permissions

Returned by `PaymentsModule.get_permissions()`
(relative names; the registry namespaces them as `payments.<name>`).

| Permission | Allows | Required by |
|------------|--------|-------------|
| `payments.record.read` | Read cobros, their allocations and refunds; read the patient ledger and pending charges; **download the printable receipt PDF**. | `GET /`<br>`GET /{payment_id}`<br>`GET /{payment_id}/receipt.pdf`<br>`GET /{payment_id}/refunds`<br>`GET /patients/{patient_id}/ledger`<br>`GET /patients/{patient_id}/pending-charges`<br>`GET /budgets/{budget_id}/allocations`<br>`POST /summary/by-budgets`<br>`POST /summary/by-patients`<br>`GET /filters/budgets-by-status`<br>`GET /filters/patients-with-debt` |
| `payments.record.write` | Record a new cobro and re-distribute an existing one across allocation targets. | `POST /`<br>`POST /{payment_id}/reallocate` |
| `payments.record.refund` | Issue a refund against a recorded cobro. Split out from `write` so a clinic can let receptionists take money without letting them give it back. | `POST /{payment_id}/refunds` |
| `payments.reports.read` | Read the payment dashboards (collected, trends, by method / professional, aging receivables, refunds). Clinical-only roles deliberately lack it. | `GET /reports/summary`<br>`GET /reports/trends`<br>`GET /reports/by-method`<br>`GET /reports/by-professional`<br>`GET /reports/aging-receivables`<br>`GET /reports/refunds` |

Paths are relative to `/api/v1/payments`.

**Receipt download is read-gated, not write-gated.** Handing a patient
their receipt is a front-desk read of data that already exists; it
creates nothing. The receipt number is assigned when the cobro is
recorded, so printing has no side effect.

## Role assignment

See `backend/app/core/auth/permissions.py` for the canonical role table,
merged at runtime with `manifest.role_permissions` in
`backend/app/modules/payments/__init__.py`. Current module grants:

| Role | Grants |
|------|--------|
| `admin` | `*` |
| `dentist` | `record.read`, `record.write`, `record.refund`, `reports.read` |
| `receptionist` | `record.read`, `record.write`, `reports.read` |
| `assistant` | `record.read`, `record.write` |
| `hygienist` | `record.read` |

Clinic admins may grant `payments.record.refund` to receptionists from
the roles UI — no code change required.

## Adding a new permission

1. Add the relative name to `get_permissions()` in
   `backend/app/modules/payments/__init__.py`.
2. Grant it to the relevant role(s) in `manifest.role_permissions`.
3. Add a row to the table above.
4. Annotate the endpoint(s) with `Depends(require_permission(...))`.
5. Update `frontend/app/config/permissions.ts` if it gates UI.
