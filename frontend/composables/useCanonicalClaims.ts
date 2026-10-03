import type { ApiErrorInfo, CanonicalClaim } from '~/types'

export interface ClaimDraft { statement: string; entities: string[]; scope?: string; validFrom?: string; source?: string }

/**
 * Canonical truth per organization (GET/POST/PATCH /api/organizations/{id}/canonical-claims). Writes carry X-Actor (a
 * human operator name; the server rejects machine-looking names). Retire = PATCH status=retired (soft; never delete).
 * Nothing is optimistic: every write refetches.
 */
export function useCanonicalClaims(orgId: MaybeRefOrGetter<string | null | undefined>) {
  const actor = ref('operator')
  const submitting = ref(false)
  const writeError = ref<ApiErrorInfo | null>(null)
  const res = useApiData<unknown, CanonicalClaim[]>(
    computed(() => `canonical-claims:${toValue(orgId) ?? 'none'}`),
    () => apiFetch(`/api/organizations/${encodeURIComponent(String(toValue(orgId)))}/canonical-claims`, { query: { include_retired: true } }),
    normCanonicalClaims,
    { enabled: () => !!toValue(orgId) }
  )
  const headers = () => ({ 'X-Actor': actor.value.trim() || 'operator' })
  const body = (d: ClaimDraft) => ({
    statement: d.statement, entities: d.entities, scope: d.scope || null, valid_from: d.validFrom || null, source: d.source || null
  })

  async function write(path: string, method: 'POST' | 'PATCH', payload: unknown): Promise<boolean> {
    submitting.value = true; writeError.value = null
    try {
      await apiFetch(path, { method, body: payload, headers: headers() })
      await res.refresh()
      return true
    } catch (e) { writeError.value = e as ApiErrorInfo; return false } finally { submitting.value = false }
  }
  const base = () => `/api/organizations/${encodeURIComponent(String(toValue(orgId)))}/canonical-claims`
  return {
    ...res, actor, submitting, writeError,
    add: (d: ClaimDraft) => write(base(), 'POST', body(d)),
    edit: (id: string, d: ClaimDraft) => write(`${base()}/${encodeURIComponent(id)}`, 'PATCH', body(d)),
    retire: (id: string) => write(`${base()}/${encodeURIComponent(id)}`, 'PATCH', { status: 'retired' })
  }
}
