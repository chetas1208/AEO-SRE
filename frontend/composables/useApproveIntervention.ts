import type { ApiErrorInfo, ProposedChange } from '~/types'

export type OverrideKind = 'approve' | 'reject' | 'modify' | 'execute' | 'executed'

/**
 * Human-override calls. Nothing is applied optimistically: callers must re-fetch incident/interventions
 * and render whatever state the backend reports. Choosing an alternative or Observe = approving that candidate's id.
 */
export function useApproveIntervention(incidentId: MaybeRefOrGetter<string>) {
  const submitting = ref(false)
  const error = ref<ApiErrorInfo | null>(null)
  const message = ref<string | null>(null)

  const refetch = () => Promise.all([
    refreshNuxtData(`incident:${toValue(incidentId)}`),
    refreshNuxtData(`incident:${toValue(incidentId)}:interventions`)
  ])

  async function call(kind: OverrideKind, interventionId: string, body?: Record<string, unknown>) {
    submitting.value = true
    error.value = null
    message.value = null
    try {
      const out = await apiFetch(`/api/interventions/${encodeURIComponent(interventionId)}/${kind}`, { method: 'POST', body: body ?? {} })
      await refetch()
      return out
    } catch (e) {
      if (isAlreadyApplied(e)) { // repeated mutation: already recorded. Show the server's state, not an error.
        message.value = 'Already recorded. Showing the current state from the backend.'
        await refetch()
        return null
      }
      error.value = e as ApiErrorInfo
      // The proposal or its guard verdict moved under the operator: reload so the new verdict/digest is what they see.
      if (['APPROVAL_DIGEST_MISMATCH', 'CHANGE_GUARD_BLOCKED'].includes(error.value.code ?? '')) await refetch().catch(() => undefined)
      throw e
    } finally {
      submitting.value = false
    }
  }

  return {
    submitting, error, message,
    /** `reviewReason` is required by the guard for REQUIRE_REVIEW verdicts; the server binds the approval to the proposal digest. */
    approve: (id: string, note?: string, reviewReason?: string) => call('approve', id, {
      ...(note ? { note } : {}),
      ...(reviewReason ? { review_reason: reviewReason } : {})
    }),
    reject: (id: string, note?: string, reasonCode?: string) => call('reject', id, {
      ...(note ? { note } : {}),
      ...(reasonCode ? { reason_code: reasonCode } : {})
    }),
    modify: (id: string, modifiedChange: ProposedChange, note?: string) => call('modify', id, { modified_change: modifiedChange, ...(note ? { note } : {}) }),
    /** Explicit (re-)activation with the default manual executor; approving already activates the experiment. */
    execute: (id: string) => call('execute', id),
    /** A human reports that they applied the intervention package. Never optimistic: callers re-fetch. */
    recordExecuted: (id: string, body: { executedAt?: string; referenceUrl?: string; note?: string; actualChange?: string }) =>
      call('executed', id, {
        ...(body.executedAt ? { executed_at: body.executedAt } : {}),
        ...(body.referenceUrl ? { reference_url: body.referenceUrl } : {}),
        ...(body.note ? { note: body.note } : {}),
        ...(body.actualChange ? { actual_change: body.actualChange } : {})
      })
  }
}
