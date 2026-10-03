import type { ApiErrorInfo } from '~/types'

/**
 * POST /api/experiments/:id/verify (202 + job). Never computes anything client-side: the backend gates on the
 * verification window (EXPERIMENT_NOT_VERIFIABLE_YET + details.eligible_at) and computes outcome and reward.
 */
export function useVerifyExperiment(id: MaybeRefOrGetter<string>) {
  const submitting = ref(false)
  const error = ref<ApiErrorInfo | null>(null)
  const queued = ref(false)

  async function verify() {
    submitting.value = true
    error.value = null
    queued.value = false
    try {
      await apiFetch(`/api/experiments/${encodeURIComponent(toValue(id))}/verify`, { method: 'POST' })
      queued.value = true
      await refreshNuxtData(`experiment:${toValue(id)}`)
    } catch (e) {
      if (isAlreadyApplied(e)) { await refreshNuxtData(`experiment:${toValue(id)}`); return }
      error.value = e as ApiErrorInfo
    } finally {
      submitting.value = false
    }
  }
  return { submitting, error, queued, verify }
}
