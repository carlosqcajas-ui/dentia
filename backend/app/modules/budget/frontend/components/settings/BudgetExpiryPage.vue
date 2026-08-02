<script setup lang="ts">
const { t } = useI18n()
const { settings, loading, saving, fetch, update } = useBudgetSettings()

const validityDays = ref(30)
const autoCloseDays = ref(30)
const manualTotalDefault = ref(false)

watch(
  settings,
  (s) => {
    if (s) {
      validityDays.value = s.budget_expiry_days
      autoCloseDays.value = s.plan_auto_close_days_after_expiry
      manualTotalDefault.value = s.budget_manual_total_default
    }
  }
)

onMounted(fetch)

async function save() {
  await update({
    budget_expiry_days: validityDays.value,
    plan_auto_close_days_after_expiry: autoCloseDays.value,
    budget_manual_total_default: manualTotalDefault.value,
  })
}
</script>

<template>
  <UCard v-if="!loading">
    <div class="space-y-4">
      <UFormField :label="t('budget.settings.expiry.validityDays')" :hint="t('budget.settings.expiry.validityHelp')">
        <UInput v-model.number="validityDays" type="number" :min="7" :max="180" />
      </UFormField>
      <UFormField :label="t('budget.settings.expiry.autoCloseDays')" :hint="t('budget.settings.expiry.autoCloseHelp')">
        <UInput v-model.number="autoCloseDays" type="number" :min="7" :max="180" />
      </UFormField>
      <UFormField :hint="t('budget.settings.manualTotal.help')">
        <UCheckbox
          v-model="manualTotalDefault"
          :label="t('budget.settings.manualTotal.label')"
        />
      </UFormField>
    </div>
    <template #footer>
      <div class="flex justify-end">
        <UButton color="primary" :loading="saving" @click="save">
          {{ t('budget.settings.expiry.save') }}
        </UButton>
      </div>
    </template>
  </UCard>
</template>
