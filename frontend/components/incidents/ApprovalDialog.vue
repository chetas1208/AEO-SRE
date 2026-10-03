<script setup lang="ts">
import type { InterventionCandidate, EvidenceItem } from '~/types'

// UI.md §24. Native <dialog> gives modal focus trapping and Esc to close. No optimistic mutation:
// result text reflects only what the backend returned.
const props = defineProps<{ candidate: InterventionCandidate | null; evidence: EvidenceItem[] | null; incidentId: string }>()
const emit = defineEmits<{ close: []; done: [] }>()
const el = ref<HTMLDialogElement | null>(null)
const api = useApproveIntervention(() => props.incidentId)
const submitted = ref(false)
const note = ref('')
const alreadyMsg = ref<string | null>(null)
const reviewReason = ref('')

watch(() => props.candidate, (c) => {
  if (c) { submitted.value = false; alreadyMsg.value = null; note.value = ''; reviewReason.value = ''; api.error.value = null; nextTick(() => el.value?.showModal()) } else el.value?.close()
})

const c = computed(() => props.candidate)
const change = computed(() => c.value?.proposedChange ?? null)
const isObserve = computed(() => c.value?.action === 'observe')
const gate = computed(() => approveGate(c.value?.guard, reviewReason.value))
const top = computed(() => (props.evidence ?? []).slice().sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0)).slice(0, 3))

async function approve() {
  if (!c.value || !gate.value.allowed) return
  try {
    const out = await api.approve(c.value.id, note.value || undefined, gate.value.needsReason ? reviewReason.value.trim() : undefined)
    if (out === null) { alreadyMsg.value = api.message.value; emit('done'); return }
  } catch { return }
  alreadyMsg.value = null
  submitted.value = true // approving ACTIVATES the experiment on the backend; the incident view re-fetches
  emit('done')
}
</script>

<template>
  <dialog ref="el" aria-labelledby="approval-title" @close="$emit('close')" @cancel="$emit('close')">
    <div v-if="c" class="dlg">
      <p class="dim" data-testid="approval-explainer">
        Approving activates an experiment. {{ isObserve ? 'Nothing is changed: the system starts observing.' : 'The change below is applied by you (the manual executor) or by an adapter you select; AEO SRE records the exact action and then measures the outcome. Nothing is changed until then.' }}
      </p>
      <h2 id="approval-title">{{ isObserve ? 'Confirm: Observe' : 'Approve & Start Experiment' }}</h2>
      <dl class="kv">
        <dt>Intervention</dt><dd>{{ c.title || actionLabel(c.action) }} <span class="meta">({{ actionLabel(c.action) }})</span></dd>
        <dt>Target</dt><dd>{{ c.executionTarget ?? change?.resource ?? change?.target ?? (isObserve ? 'None (no external mutation)' : 'not reported') }}</dd>
        <dt>Risk level</dt><dd>{{ humanize(c.risk) }}</dd>
        <dt>Rollback mechanism</dt><dd>{{ c.rollback ?? 'not reported' }}</dd>
        <dt>Executor</dt><dd>{{ executorLabel(c.executor) }}<span v-if="c.dryRun" class="badge tone-warn" style="margin-left: 6px">GitHub dry-run preview</span></dd>
        <dt>Observation window</dt><dd>{{ c.observationWindow ?? 'starts when execution is recorded' }}</dd>
        <dt>Selection basis</dt><dd>{{ basisLabel(c.selectionBasis, c.policyVersion, c.relatedExperiments) }}</dd>
      </dl>
      <section>
        <h3>Exact proposed change</h3>
        <pre v-if="change?.diff" data-testid="approval-diff">{{ change.diff }}</pre>
        <p v-else-if="isObserve" class="dim">Incident remains open. System monitors the next measurement window. No external mutation occurs.</p>
        <p v-else class="tone-warn">The API did not provide an exact diff for this change. Review before approving.</p>
        <p v-if="changeFilePaths(change).length" class="meta">Files: {{ changeFilePaths(change).join(', ') }}</p>
      </section>
      <section>
        <h3>Evidence summary</h3>
        <ul v-if="top.length" style="margin: 0; padding-left: 18px">
          <li v-for="e in top" :key="e.id">{{ e.title }} <span class="meta">· {{ humanize(e.status) }}<template v-if="e.confidence != null"> · {{ e.confidence.toFixed(2) }}</template></span></li>
        </ul>
        <p v-else class="dim">No evidence rows loaded.</p>
      </section>
      <GuardVerdict :guard="c.guard" :review-reason="reviewReason" @update:review-reason="reviewReason = $event" />
      <label class="stack sm"><span class="meta">Approval note (optional)</span><textarea v-model="note" rows="2" /></label>
      <p v-if="api.error.value" class="tone-bad" role="alert">Approval failed: {{ api.error.value.message }} {{ api.error.value.code === 'APPROVAL_DIGEST_MISMATCH' || api.error.value.code === 'CHANGE_GUARD_BLOCKED' ? '' : api.error.value.detail }}</p>
      <p v-if="alreadyMsg" class="tone-warn" role="status">{{ alreadyMsg }}</p>
      <p v-if="submitted" class="tone-good" role="status">Approval submitted. Waiting for backend state; the intervention package appears on the incident once the experiment is activated.</p>
      <div class="row">
        <button v-if="!submitted" class="primary" type="button" :disabled="api.submitting.value || !gate.allowed" :title="gate.allowed ? undefined : gate.message ?? undefined" data-testid="approve-submit" @click="approve">
          {{ api.submitting.value ? 'Submitting…' : isObserve ? 'Confirm Observe' : 'Approve & Start Experiment' }}
        </button>
        <button type="button" @click="$emit('close')">{{ submitted ? 'Close' : 'Cancel' }}</button>
      </div>
    </div>
  </dialog>
</template>
