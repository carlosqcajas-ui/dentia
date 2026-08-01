# 0019 — Remove patient self-service budget acceptance/rejection

- **Status:** accepted; supersedes 0006-budget-public-link-2-factor-auth
- **Date:** 2026-08-01
- **Deciders:** Product owner
- **Tags:** modules, budget, simplification, regionalization

## Context

ADR 0006 introduced a public, unauthenticated, 2-factor-verified link
(`/p/budget/{token}`) that let a patient open a budget remotely, verify
their identity (phone last-4 / date of birth / manual code), and
accept or reject the budget themselves — with a drawn/click signature
and a tamper-evident signed PDF.

For this deployment, the product decision is that **patients should
never decide directly in the system** — acceptance/rejection is always
recorded by clinic staff (in person or after a phone call), matching
how the clinic actually operates and reducing the amount of
infrastructure (2FA, rate limiting, lockout, cookie sessions, reminder
cron, access-log retention) that has to be built, tested and reasoned
about for a single small-clinic deployment. The intent (from the
requesting conversation) is to possibly reintroduce a patient-facing
channel later, in a different shape — this ADR only retires the
current implementation.

## Decision

Remove the entire patient-facing public budget flow:

- `backend/app/modules/budget/public_router.py` and its five endpoints
  (`/public/budgets/{token}/{meta,verify,accept,reject,pdf/signed}`
  plus the bare `GET /public/budgets/{token}`).
- The `Budget` columns `public_token`, `viewed_at`,
  `last_reminder_sent_at`, `public_auth_method`,
  `public_auth_secret_hash`, `public_locked_at` (migration `bud_0006`).
- The `budget_access_logs` table and its 90-day purge cron.
- The `send_budget_reminders` cron and the manual `/send-reminder`
  staff endpoint (both only existed to nudge patients toward the
  public link).
- `budget.viewed` / `budget.reminder_sent` events (`EventType` members
  removed) and their `patient_timeline` consumers.
- Frontend: `/p/budget/[token].vue`, `PublicBudgetLinkCard.vue`,
  `BudgetVerifyForm.vue`, `usePublicBudget.ts`, `SetPublicCodeModal.vue`,
  and the `public` Nuxt layout.
- `BUDGET_PUBLIC_SECRET_KEY` setting.

**Kept, unchanged:** the staff-authenticated
`POST /budgets/{id}/accept`, `/reject`, and `/accept-in-clinic`
endpoints, the underlying `BudgetWorkflowService.accept_budget` /
`reject_budget` (signature capture, tamper-evident signed PDF,
`budget.accepted` / `budget.rejected` events), and everything that
consumes those events (`treatment_plan`, `billing`). A budget can
still be resent as a new draft version (`/resend`) for staff to hand
or send again through whatever channel they use outside the app.

## Consequences

### Good

- Meaningfully less code and infrastructure to maintain: no 2FA, no
  signed cookie sessions, no rate-limit/lockout state machine, no
  reminder cron, no access-log retention job.
- Matches the clinic's actual workflow (staff talks to the patient,
  then records the decision) instead of a remote-consent model built
  for a different market/scale.
- No impact on `treatment_plan` or `billing` — both react to
  `budget.accepted` / `budget.rejected`, which fire identically
  regardless of who called the workflow method.

### Bad / accepted trade-offs

- Loses the "verifiably the patient, not staff" consent trail that the
  2FA link + signature-over-the-wire provided. Acceptance now rests on
  staff's record of the interaction (optionally with an in-clinic
  signature capture), same as any paper-based process.
- Automated reminders for un-responded budgets are gone; follow-up is
  now a manual staff task (or a candidate for a future feature under a
  new ADR).
- Historical `budget_access_logs` rows and prior `public_token`s are
  dropped by the migration — irrecoverable, but this deployment is
  pre-production so no real audit trail is lost.

## Alternatives considered

- **Keep the public link but drop 2FA** — rejected: still requires the
  whole cookie-session + link infrastructure for no real benefit once
  identity verification is gone.
- **Feature-flag it off per clinic** — rejected as premature
  complexity for a single-clinic deployment; easier to reintroduce
  properly (possibly differently shaped) later than to maintain a
  toggle for code nobody uses.

## How to verify the rule still holds

- `grep -rn "public_token\|BudgetAccessLog\|public_auth_method" backend/app/modules/budget backend/app/modules/treatment_plan backend/app/modules/patient_timeline` → no matches outside migration history.
- `docker compose exec backend python -c "import app.main"` → imports
  cleanly (module registry, routers, event handlers all resolve).
- `GET /api/v1/budget/public/budgets/<any-uuid>/meta` → 404 (route no
  longer exists).

## References

- Supersedes `docs/adr/0006-budget-public-link-2-factor-auth.md`.
- `backend/app/modules/budget/migrations/versions/bud_0006_drop_public_link.py`
- `backend/app/modules/budget/workflow.py` (`accept_budget`, `reject_budget`)
- `backend/app/modules/budget/router.py` (`/accept`, `/reject`, `/accept-in-clinic`)
