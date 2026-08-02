import { test, expect } from './_fixtures'

/**
 * RBAC smoke — asserts the sidebar reflects what each role is allowed
 * to see. Backend-side permission enforcement has its own pytest
 * coverage; this file verifies the UI hides / shows the right entry
 * points so we don't regress the user experience.
 *
 * Labels come from the nav i18n block; keep the regexes loose so
 * copy-edits don't break the suite.
 */

const LABELS = {
  patients: /patients|pacientes/i,
  schedule: /schedule|agenda|citas|appointments/i,
  plans: /^(treatment plans|planes de tratamiento)$/i,
  quotes: /quotes|budgets|presupuestos/i,
  payments: /payments|cobros|paiements/i,
  // Billing has no navigation entry — the clinic does not emit formal
  // invoices. Asserted absent below so a re-added nav entry fails CI.
  invoices: /invoices|facturas/i,
  reports: /reports|informes/i
}

test.describe('hygienist sees clinical + scheduling, no reports', () => {
  test.use({ role: 'hygienist' })

  test('navigation is filtered to read-only flows', async ({ loggedIn }) => {
    const nav = loggedIn.getByRole('navigation').first()
    await expect(nav.getByRole('link', { name: LABELS.patients })).toBeVisible()
    await expect(nav.getByRole('link', { name: LABELS.schedule })).toBeVisible()
    // Reports require reports.billing.read which hygienist lacks.
    await expect(nav.getByRole('link', { name: LABELS.reports })).toHaveCount(0)
  })
})

test.describe('receptionist sees patients + schedule + payments', () => {
  test.use({ role: 'receptionist' })

  test('navigation is filtered to front-desk flows', async ({ loggedIn }) => {
    const nav = loggedIn.getByRole('navigation').first()
    await expect(nav.getByRole('link', { name: LABELS.patients })).toBeVisible()
    await expect(nav.getByRole('link', { name: LABELS.schedule })).toBeVisible()
    await expect(nav.getByRole('link', { name: LABELS.payments })).toBeVisible()
    await expect(nav.getByRole('link', { name: LABELS.invoices })).toHaveCount(0)
  })
})

test.describe('dentist has full clinical access', () => {
  test.use({ role: 'dentist' })

  test('nav shows every clinical flow', async ({ loggedIn }) => {
    const nav = loggedIn.getByRole('navigation').first()
    for (const label of [
      LABELS.patients,
      LABELS.schedule,
      LABELS.plans,
      LABELS.quotes,
      LABELS.payments
    ]) {
      await expect(nav.getByRole('link', { name: label })).toBeVisible()
    }
    await expect(nav.getByRole('link', { name: LABELS.invoices })).toHaveCount(0)
  })
})
