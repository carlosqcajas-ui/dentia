<script setup lang="ts">
import type { ApiResponse, PaginatedResponse } from '~/types'

// Unlisted operator utility — not registered in nav or the settings
// registry. Reachable only via direct URL, gated by X-Operator-Key
// (checked server-side against OPERATOR_SECRET_KEY). Not part of the
// normal RBAC/session flow: there is no logged-in user at this point.
definePageMeta({
  layout: 'guest'
})

interface ClinicSummary {
  id: string
  name: string
  tax_id: string
  admin_count: number
  active_user_count: number
  total_user_count: number
}

interface ClinicCreateResult {
  clinic_id: string
  admin_user_id: string
  admin_email: string
  temp_password: string
}

interface ModuleInfo {
  name: string
  version: string
  state: 'installed' | 'uninstalled' | 'to_install' | 'to_remove' | 'to_upgrade' | string
  category: string
  removable: boolean
  auto_install: boolean
  summary: string
  depends: string[]
}

const api = useApi()
const toast = useToast()

const operatorKey = ref('')
const keyConfirmed = ref(false)

// sessionStorage is browser-only — this page renders once server-side
// (no session yet) before hydrating client-side, where the stored key
// (if any) takes over.
onMounted(() => {
  const stored = sessionStorage.getItem('operator_key')
  if (stored) {
    operatorKey.value = stored
    keyConfirmed.value = true
    loadClinics()
    loadModules()
  }
})

function saveKey() {
  if (!operatorKey.value.trim()) return
  sessionStorage.setItem('operator_key', operatorKey.value.trim())
  keyConfirmed.value = true
  loadClinics()
  loadModules()
}

function authHeaders() {
  return { 'X-Operator-Key': operatorKey.value.trim() }
}

// --- Create clinic ------------------------------------------------------

const isCreating = ref(false)
const createError = ref('')
const lastCreated = ref<ClinicCreateResult | null>(null)

// Single-market deployment (La Paz, Bolivia) — timezone and currency
// are fixed, not user-editable.
const form = reactive({
  clinic_name: '',
  clinic_tax_id: '',
  timezone: 'America/La_Paz',
  currency: 'BOB',
  admin_first_name: '',
  admin_last_name: '',
  admin_email: ''
})

async function createClinic() {
  createError.value = ''
  isCreating.value = true
  try {
    const res = await api.post<ApiResponse<ClinicCreateResult>>(
      '/api/v1/auth/operator/clinics',
      { ...form },
      { skipAuth: true, headers: authHeaders() }
    )
    lastCreated.value = res.data
    Object.assign(form, {
      clinic_name: '',
      clinic_tax_id: '',
      admin_first_name: '',
      admin_last_name: '',
      admin_email: ''
    })
    await loadClinics()
  } catch (err: unknown) {
    const e = err as { statusCode?: number, data?: { detail?: string } }
    createError.value = e.data?.detail || 'No se pudo crear la clínica. Revisa la clave de operador.'
  } finally {
    isCreating.value = false
  }
}

function copyPassword() {
  if (!lastCreated.value) return
  navigator.clipboard.writeText(lastCreated.value.temp_password)
  toast.add({ title: 'Contraseña copiada', color: 'success' })
}

// --- Clinic list ----------------------------------------------------------

const clinics = ref<ClinicSummary[]>([])
const isLoadingList = ref(false)
const listError = ref('')
const busyClinicId = ref<string | null>(null)

async function loadClinics() {
  isLoadingList.value = true
  listError.value = ''
  try {
    const res = await api.get<PaginatedResponse<ClinicSummary>>(
      '/api/v1/auth/operator/clinics',
      { skipAuth: true, headers: authHeaders() }
    )
    clinics.value = res.data
  } catch (err: unknown) {
    const e = err as { statusCode?: number }
    listError.value = e.statusCode === 401
      ? 'Clave de operador inválida.'
      : 'No se pudo cargar la lista de clínicas.'
  } finally {
    isLoadingList.value = false
  }
}

async function toggleClinic(clinic: ClinicSummary) {
  busyClinicId.value = clinic.id
  const action = clinic.active_user_count > 0 ? 'deactivate' : 'reactivate'
  try {
    await api.post(
      `/api/v1/auth/operator/clinics/${clinic.id}/${action}`,
      null,
      { skipAuth: true, headers: authHeaders() }
    )
    toast.add({
      title: action === 'deactivate' ? 'Clínica dada de baja' : 'Clínica reactivada',
      color: 'success'
    })
    await loadClinics()
  } catch {
    toast.add({ title: 'La operación falló', color: 'error' })
  } finally {
    busyClinicId.value = null
  }
}

// --- Modules ----------------------------------------------------------
//
// Install state is system-wide (one shared set of active modules for
// this whole deployment, not per clinic) — see plugins/router.py.

const modules = ref<ModuleInfo[]>([])
const isLoadingModules = ref(false)
const modulesError = ref('')
const busyModuleName = ref<string | null>(null)
const isRestarting = ref(false)

const pendingRestart = computed(() =>
  modules.value.some(m => m.state === 'to_install' || m.state === 'to_remove' || m.state === 'to_upgrade')
)

async function loadModules() {
  isLoadingModules.value = true
  modulesError.value = ''
  try {
    const res = await api.get<ApiResponse<ModuleInfo[]>>(
      '/api/v1/modules/-/operator',
      { skipAuth: true, headers: authHeaders() }
    )
    modules.value = res.data.sort((a, b) => a.name.localeCompare(b.name))
  } catch {
    modulesError.value = 'No se pudo cargar la lista de módulos.'
  } finally {
    isLoadingModules.value = false
  }
}

async function toggleModule(mod: ModuleInfo) {
  busyModuleName.value = mod.name
  const isActive = mod.state === 'installed' || mod.state === 'to_install'
  const action = isActive ? 'uninstall' : 'install'
  try {
    await api.post(
      `/api/v1/modules/-/operator/${mod.name}/${action}`,
      null,
      { skipAuth: true, headers: authHeaders() }
    )
    toast.add({
      title: action === 'install' ? 'Módulo marcado para instalar' : 'Módulo marcado para quitar',
      description: 'Reinicia el backend para aplicar el cambio.',
      color: 'success'
    })
    await loadModules()
  } catch (err: unknown) {
    const e = err as { data?: { detail?: string } }
    toast.add({ title: e.data?.detail || 'La operación falló', color: 'error' })
  } finally {
    busyModuleName.value = null
  }
}

async function restartBackend() {
  isRestarting.value = true
  try {
    await api.post('/api/v1/modules/-/operator/restart', null, { skipAuth: true, headers: authHeaders() })
    toast.add({ title: 'Reinicio programado — el backend estará abajo unos segundos', color: 'success' })
  } catch {
    toast.add({ title: 'No se pudo programar el reinicio', color: 'error' })
  } finally {
    isRestarting.value = false
  }
}

function moduleStateLabel(state: string): string {
  return {
    installed: 'Instalado',
    uninstalled: 'No instalado',
    to_install: 'Se instalará al reiniciar',
    to_remove: 'Se quitará al reiniciar',
    to_upgrade: 'Se actualizará al reiniciar'
  }[state] || state
}
</script>

<template>
  <div class="w-full max-w-[720px] p-6 space-y-6">
    <div>
      <h1 class="text-h1 text-default">
        Panel de operador
      </h1>
      <p class="text-caption text-muted mt-1">
        Alta y baja manual de clínicas. No forma parte del panel normal — guarda esta URL aparte.
      </p>
    </div>

    <!-- Operator key gate -->
    <UCard v-if="!keyConfirmed">
      <form
        class="space-y-4"
        @submit.prevent="saveKey"
      >
        <UFormField label="Clave de operador">
          <UInput
            v-model="operatorKey"
            type="password"
            class="w-full"
            placeholder="X-Operator-Key"
            autocomplete="off"
          />
        </UFormField>
        <UButton
          type="submit"
          color="primary"
          variant="soft"
          block
        >
          Entrar
        </UButton>
      </form>
    </UCard>

    <template v-else>
      <!-- Create clinic -->
      <UCard>
        <template #header>
          <h2 class="text-h3 text-default">
            Nueva clínica
          </h2>
        </template>

        <div
          v-if="createError"
          class="alert-surface-danger rounded-token-md px-3 py-2 mb-4 text-body"
        >
          {{ createError }}
        </div>

        <div
          v-if="lastCreated"
          class="alert-surface-success rounded-token-md px-3 py-3 mb-4 space-y-1"
        >
          <p class="text-body font-medium">
            Clínica creada. Copia la contraseña ahora — no se vuelve a mostrar.
          </p>
          <p class="text-caption">
            Admin: {{ lastCreated.admin_email }}
          </p>
          <div class="flex items-center gap-2">
            <code class="text-caption bg-elevated px-2 py-1 rounded-token-sm">{{ lastCreated.temp_password }}</code>
            <UButton
              size="xs"
              variant="ghost"
              icon="i-lucide-copy"
              @click="copyPassword"
            >
              Copiar
            </UButton>
          </div>
        </div>

        <form
          class="space-y-4"
          @submit.prevent="createClinic"
        >
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <UFormField label="Nombre de la clínica">
              <UInput
                v-model="form.clinic_name"
                class="w-full"
                required
              />
            </UFormField>
            <UFormField label="NIT">
              <UInput
                v-model="form.clinic_tax_id"
                class="w-full"
                required
              />
            </UFormField>
          </div>

          <p class="text-caption text-muted">
            Zona horaria: América/La Paz · Divisa: BOB (fijas para este sistema)
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <UFormField label="Nombre del encargado">
              <UInput
                v-model="form.admin_first_name"
                class="w-full"
                required
              />
            </UFormField>
            <UFormField label="Apellido del encargado">
              <UInput
                v-model="form.admin_last_name"
                class="w-full"
                required
              />
            </UFormField>
          </div>

          <UFormField label="Email del encargado (será su usuario)">
            <UInput
              v-model="form.admin_email"
              type="email"
              class="w-full"
              required
            />
          </UFormField>

          <UButton
            type="submit"
            color="primary"
            variant="soft"
            block
            :loading="isCreating"
            :disabled="isCreating"
          >
            Crear clínica
          </UButton>
        </form>
      </UCard>

      <!-- Clinic list -->
      <UCard>
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-h3 text-default">
              Clínicas ({{ clinics.length }})
            </h2>
            <UButton
              size="xs"
              variant="ghost"
              icon="i-lucide-refresh-cw"
              :loading="isLoadingList"
              @click="loadClinics"
            >
              Actualizar
            </UButton>
          </div>
        </template>

        <p
          v-if="listError"
          class="text-body text-danger"
        >
          {{ listError }}
        </p>

        <div class="divide-y divide-default">
          <div
            v-for="clinic in clinics"
            :key="clinic.id"
            class="flex items-center justify-between py-3"
          >
            <div>
              <p class="text-body font-medium text-default">
                {{ clinic.name }}
              </p>
              <p class="text-caption text-muted">
                {{ clinic.tax_id }} · {{ clinic.active_user_count }}/{{ clinic.total_user_count }} usuarios activos
              </p>
            </div>
            <UButton
              size="xs"
              :color="clinic.active_user_count > 0 ? 'error' : 'success'"
              variant="soft"
              :loading="busyClinicId === clinic.id"
              @click="toggleClinic(clinic)"
            >
              {{ clinic.active_user_count > 0 ? 'Dar de baja' : 'Reactivar' }}
            </UButton>
          </div>
          <p
            v-if="!clinics.length && !isLoadingList"
            class="text-caption text-muted py-4 text-center"
          >
            No hay clínicas todavía.
          </p>
        </div>
      </UCard>

      <!-- Modules -->
      <UCard>
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-h3 text-default">
              Módulos ({{ modules.length }})
            </h2>
            <UButton
              size="xs"
              variant="ghost"
              icon="i-lucide-refresh-cw"
              :loading="isLoadingModules"
              @click="loadModules"
            >
              Actualizar
            </UButton>
          </div>
        </template>

        <p class="text-caption text-muted mb-3">
          El estado de instalación es único para todo el sistema — no es por clínica.
        </p>

        <div
          v-if="pendingRestart"
          class="alert-surface-warning rounded-token-md px-3 py-2 mb-4 flex items-center justify-between gap-3"
        >
          <span class="text-body">Hay cambios pendientes de aplicar.</span>
          <UButton
            size="xs"
            color="warning"
            variant="solid"
            :loading="isRestarting"
            @click="restartBackend"
          >
            Reiniciar backend
          </UButton>
        </div>

        <p
          v-if="modulesError"
          class="text-body text-danger"
        >
          {{ modulesError }}
        </p>

        <div class="divide-y divide-default">
          <div
            v-for="mod in modules"
            :key="mod.name"
            class="flex items-center justify-between py-3 gap-3"
          >
            <div class="min-w-0">
              <p class="text-body font-medium text-default">
                {{ mod.name }}
                <span class="text-caption text-muted">v{{ mod.version }}</span>
              </p>
              <p class="text-caption text-muted truncate">
                {{ mod.summary || moduleStateLabel(mod.state) }}
              </p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <UBadge
                size="sm"
                variant="subtle"
                :color="mod.state === 'installed' ? 'success' : mod.state.startsWith('to_') ? 'warning' : 'neutral'"
              >
                {{ moduleStateLabel(mod.state) }}
              </UBadge>
              <UButton
                size="xs"
                :color="mod.state === 'installed' || mod.state === 'to_install' ? 'error' : 'success'"
                variant="soft"
                :disabled="!mod.removable && mod.state !== 'uninstalled'"
                :loading="busyModuleName === mod.name"
                @click="toggleModule(mod)"
              >
                {{ mod.state === 'installed' || mod.state === 'to_install' ? 'Quitar' : 'Instalar' }}
              </UButton>
            </div>
          </div>
          <p
            v-if="!modules.length && !isLoadingModules"
            class="text-caption text-muted py-4 text-center"
          >
            No hay módulos.
          </p>
        </div>
      </UCard>
    </template>
  </div>
</template>
