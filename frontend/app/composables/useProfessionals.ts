import type { Professional, PaginatedResponse } from '~/types'

const PROFESSIONAL_COLORS = [
  '#3B82F6', // blue
  '#10B981', // emerald
  '#8B5CF6', // violet
  '#F59E0B', // amber
  '#EF4444', // red
  '#EC4899', // pink
  '#06B6D4', // cyan
  '#84CC16' // lime
]

const FRESH_MS = 60_000 // cached list is reused for a minute across navigations

export function useProfessionals() {
  const api = useApi()
  const { t } = useI18n()

  // Shared across every component via useState — without this each
  // useProfessionals() call got its own empty ref, so navigating between
  // agenda/treatment-plans/schedules/patient-timeline re-fetched the same
  // professionals list on every page.
  const professionals = useState<Professional[]>('professionals:list', () => [])
  const isLoading = useState<boolean>('professionals:loading', () => false)
  const error = useState<string | null>('professionals:error', () => null)
  const lastLoadedAt = useState<number>('professionals:loadedAt', () => 0)

  const professionalColors = computed<Map<string, string>>(() => {
    const map = new Map<string, string>()
    professionals.value.forEach((prof, index) => {
      const color = PROFESSIONAL_COLORS[index % PROFESSIONAL_COLORS.length]
      if (color) map.set(prof.id, color)
    })
    return map
  })

  let inFlight: Promise<void> | null = null

  async function fetchProfessionals(force = false): Promise<void> {
    const age = Date.now() - lastLoadedAt.value
    if (!force && professionals.value.length > 0 && age < FRESH_MS) return
    if (inFlight) return inFlight

    isLoading.value = true
    error.value = null

    inFlight = (async () => {
      try {
        const response = await api.get<PaginatedResponse<Professional>>('/api/v1/auth/professionals')
        professionals.value = response.data
        lastLoadedAt.value = Date.now()
      } catch (e) {
        error.value = t('professionals.toast.loadFailed')
        console.error('Failed to fetch professionals:', e)
      } finally {
        isLoading.value = false
        inFlight = null
      }
    })()

    return inFlight
  }

  function getProfessionalById(id: string): Professional | undefined {
    return professionals.value.find(p => p.id === id)
  }

  function getProfessionalColor(id: string): string {
    return professionalColors.value.get(id) || '#6B7280' // Default gray
  }

  function getProfessionalInitials(professional: Professional): string {
    const first = professional.first_name.charAt(0).toUpperCase()
    const last = professional.last_name.charAt(0).toUpperCase()
    return `${first}${last}`
  }

  function getProfessionalFullName(professional: Professional): string {
    return `${professional.first_name} ${professional.last_name}`
  }

  return {
    professionals: readonly(professionals),
    isLoading: readonly(isLoading),
    error: readonly(error),
    fetchProfessionals,
    getProfessionalById,
    getProfessionalColor,
    getProfessionalInitials,
    getProfessionalFullName
  }
}
