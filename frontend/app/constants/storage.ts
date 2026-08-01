export const STORAGE_KEYS = {
  LOCALE: 'dentia:locale',
  DENSITY: 'ui:density',
  onboardingDismissed: (clinicId: string) =>
    `dentia.settings.onboarding.dismissed:${clinicId}`
} as const
