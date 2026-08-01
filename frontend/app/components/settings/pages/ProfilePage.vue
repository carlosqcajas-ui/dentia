<script setup lang="ts">
const { t } = useI18n()
const auth = useAuth()
const api = useApi()
const toast = useToast()

const isChangingPassword = ref(false)
const isSaving = ref(false)
const passwordError = ref('')
const form = reactive({
  current_password: '',
  new_password: '',
  confirm_password: ''
})

function startChange() {
  form.current_password = ''
  form.new_password = ''
  form.confirm_password = ''
  passwordError.value = ''
  isChangingPassword.value = true
}

function cancelChange() {
  isChangingPassword.value = false
}

async function submitChange() {
  passwordError.value = ''

  if (form.new_password.length < 8) {
    passwordError.value = t('settings.passwordTooShort')
    return
  }
  if (form.new_password !== form.confirm_password) {
    passwordError.value = t('settings.passwordMismatch')
    return
  }

  isSaving.value = true
  try {
    await api.post('/api/v1/auth/change-password', {
      current_password: form.current_password,
      new_password: form.new_password
    })
    toast.add({ title: t('settings.passwordChanged'), color: 'success' })
    isChangingPassword.value = false
  } catch (err: unknown) {
    const e = err as { statusCode?: number, data?: { detail?: string } }
    passwordError.value = e.statusCode === 401
      ? t('settings.passwordCurrentWrong')
      : (e.data?.detail || t('common.serverError'))
  } finally {
    isSaving.value = false
  }
}
</script>

<template>
  <SectionCard
    icon="i-lucide-user"
    :title="t('settings.profile')"
  >
    <div
      v-if="auth.user.value"
      class="space-y-6"
    >
      <div class="flex items-center gap-4">
        <UAvatar
          :alt="auth.user.value.first_name"
          size="lg"
        />
        <div>
          <p class="font-medium text-default">
            {{ auth.user.value.first_name }} {{ auth.user.value.last_name }}
          </p>
          <p class="text-caption text-subtle">
            {{ auth.user.value.email }}
          </p>
        </div>
      </div>

      <div class="border-t border-default pt-4">
        <div
          v-if="!isChangingPassword"
          class="flex items-center justify-between"
        >
          <div>
            <p class="text-body font-medium text-default">
              {{ t('settings.password') }}
            </p>
            <p class="text-caption text-subtle">
              {{ t('settings.passwordDescription') }}
            </p>
          </div>
          <UButton
            icon="i-lucide-key-round"
            size="xs"
            variant="ghost"
            @click="startChange"
          >
            {{ t('settings.changePassword') }}
          </UButton>
        </div>

        <form
          v-else
          class="space-y-4"
          @submit.prevent="submitChange"
        >
          <div
            v-if="passwordError"
            class="alert-surface-danger rounded-token-md px-3 py-2 text-body"
          >
            {{ passwordError }}
          </div>

          <UFormField :label="t('settings.currentPassword')">
            <UInput
              v-model="form.current_password"
              type="password"
              class="w-full"
              autocomplete="current-password"
              required
            />
          </UFormField>
          <UFormField :label="t('settings.newPassword')">
            <UInput
              v-model="form.new_password"
              type="password"
              class="w-full"
              autocomplete="new-password"
              required
            />
          </UFormField>
          <UFormField :label="t('settings.confirmPassword')">
            <UInput
              v-model="form.confirm_password"
              type="password"
              class="w-full"
              autocomplete="new-password"
              required
            />
          </UFormField>

          <div class="flex justify-end gap-2">
            <UButton
              variant="ghost"
              :disabled="isSaving"
              @click="cancelChange"
            >
              {{ t('common.cancel') }}
            </UButton>
            <UButton
              type="submit"
              :loading="isSaving"
            >
              {{ t('settings.saveChanges') }}
            </UButton>
          </div>
        </form>
      </div>
    </div>
  </SectionCard>
</template>
