import { defineStore } from 'pinia'
import type { Organization, ApiErrorInfo } from '~/types'

export const useOrganizationStore = defineStore('organization', () => {
  const orgs = ref<Organization[]>([])
  const currentId = useCookie<string | null>('aeo_org', { default: () => null, sameSite: 'lax', maxAge: 60 * 60 * 24 * 365 })
  const loading = ref(false)
  const error = ref<ApiErrorInfo | null>(null)
  const loaded = ref(false)

  const current = computed(() => orgs.value.find((o) => o.id === currentId.value) ?? orgs.value[0] ?? null)

  async function load() {
    loading.value = true
    try {
      const raw = await apiFetch('/api/organizations')
      orgs.value = unwrapList(raw, 'organizations').map(normOrganization)
      if (!orgs.value.some((o) => o.id === currentId.value)) currentId.value = orgs.value[0]?.id ?? null
      error.value = null
    } catch (e) {
      error.value = e as ApiErrorInfo
    } finally {
      loading.value = false
      loaded.value = true
    }
  }

  async function add(domain: string, name?: string) {
    const raw = await apiFetch('/api/organizations', { method: 'POST', body: { domain, ...(name ? { name } : {}) } })
    const org = normOrganization(raw as any)
    orgs.value = [...orgs.value.filter((o) => o.id !== org.id), org]
    currentId.value = org.id
    return org
  }

  function select(id: string) { currentId.value = id }

  function upsert(org: Organization) {
    orgs.value = orgs.value.map((o) => (o.id === org.id ? org : o))
  }

  return { orgs, current, currentId, loading, error, loaded, load, add, select, upsert }
})
