<script setup lang="ts">
import type { ApiErrorInfo, IncidentDetail, InterventionCandidate, InterventionSet } from '~/types'

const props = defineProps<{
  incident: IncidentDetail
  set: InterventionSet | null
  pending: boolean
  error?: ApiErrorInfo | null
  evidenceError?: ApiErrorInfo | null
  evidencePending?: boolean
}>()
const emit = defineEmits<{ approve: [c: InterventionCandidate]; retry: [] }>()

const api = useApproveIntervention(() => props.incident.id)
const chosenId = ref<string | null>(null)
const mode = ref<'idle' | 'modify' | 'reject'>('idle')
const draft = ref('')
const note = ref('')
const reasonCode = ref('other')
const REJECTION_REASONS = [
  ['incorrect_root_cause', 'Incorrect root cause'],
  ['action_too_risky', 'Action too risky'],
  ['action_too_costly', 'Action too costly'],
  ['already_addressed', 'Already addressed'],
  ['insufficient_evidence', 'Insufficient evidence'],
  ['not_strategically_important', 'Not strategically important'],
  ['other', 'Other']
]
const localErr = ref<string | null>(null)
const done = ref<string | null>(null)

const items = computed(() => props.set?.items ?? [])
const recommended = computed(() => items.value.find((i) => i.selected) ?? null)
const chosen = computed(() => items.value.find((i) => i.id === chosenId.value) ?? recommended.value)
const overridden = computed(() => !!chosen.value && !!recommended.value && chosen.value.id !== recommended.value.id)
const alternatives = computed(() => items.value.filter((i) => i.id !== chosen.value?.id))
const canApprove = computed(() => {
  const a = props.incident.allowedActions?.find((x) => x.action === 'approve')
  return a ? a.enabled : ['intervention_proposed', 'awaiting_approval'].includes(props.incident.state)
})
const change = computed(() => chosen.value?.proposedChange ?? null)
const gate = computed(() => approveGate(chosen.value?.guard))
const approved = computed(() => items.value.find((i) => ['approved', 'modified'].includes(i.approvalStatus ?? '')) ?? null)
const targetText = computed(() => chosen.value?.executionTarget ?? changeFilePaths(change.value).join(', ') ?? null)

watch(items, () => { if (chosenId.value && !items.value.some((i) => i.id === chosenId.value)) chosenId.value = null })
watch(chosen, (c) => { draft.value = c?.proposedChange?.diff ?? '' }, { immediate: true })

function approve() {
  if (!chosen.value) return
  emit('approve', overridden.value ? { ...chosen.value, selectionBasis: 'manual_override' } : chosen.value)
}
async function submitModify() {
  if (!chosen.value) return
  localErr.value = null
  try {
    const out = await api.modify(chosen.value.id, { ...(change.value ?? {}), diff: draft.value }, note.value || undefined)
    done.value = out === null ? api.message.value : 'Modification submitted. Waiting for backend state.'
    mode.value = 'idle'
  } catch (e) { localErr.value = (e as ApiErrorInfo).message }
}
async function submitReject() {
  if (!chosen.value) return
  localErr.value = null
  try {
    const out = await api.reject(chosen.value.id, note.value || undefined, reasonCode.value)
    done.value = out === null ? api.message.value : 'Rejection submitted. Waiting for backend state.'
    mode.value = 'idle'
  } catch (e) { localErr.value = (e as ApiErrorInfo).message }
}
</script>

<template>
  <section class="stack" aria-label="Recommended action" data-testid="action-panel">
    <ErrorState v-if="evidenceError" :error="evidenceError" surface="Evidence" note="The recommendation is withheld because the evidence behind it could not be loaded." @retry="$emit('retry')" />
    <LoadingState v-else-if="pending && !set" message="Computing intervention policy…" />
    <ErrorState v-else-if="set?.unavailableReason && !items.length" :error="{ kind: 'unavailable', message: set.unavailableReason }" surface="Intervention policy" @retry="$emit('retry')" />
    <ErrorState v-else-if="error && !set" :error="error" surface="Intervention policy" @retry="$emit('retry')" />
    <EmptyState v-else-if="!items.length" title="No candidate interventions have been produced for this incident." :lines="['Candidates appear after investigation completes and the policy has run.']" />
    <template v-else>
      <LoadingState v-if="evidencePending" message="Fetching sources…" />
      <InterventionPackage v-if="approved?.package" :candidate="approved" :incident-id="incident.id" @recorded="$emit('retry')" />
      <div v-if="chosen" class="card selected stack sm">
        <div class="row spread wrap">
          <h2>{{ overridden ? 'Selected action (manual override)' : 'Recommended action' }}</h2>
          <StatusBadge v-if="chosen.coldStart" label="Cold-start policy" tone="policy" />
        </div>
        <p><strong>{{ chosen.title || actionLabel(chosen.action) }}</strong></p>
        <p v-if="chosen.reason" class="dim">{{ chosen.reason }}</p>
        <dl class="kv">
          <dt>Policy score</dt><dd class="mono">{{ chosen.score.toFixed(2) }}<span v-if="chosen.policyProbability != null" class="meta"> · selection probability {{ chosen.policyProbability.toFixed(2) }}</span></dd>
          <dt>Risk</dt><dd>{{ humanize(chosen.risk) }}</dd>
          <dt>Executor</dt><dd>{{ executorLabel(chosen.executor) }}</dd>
          <dt>Target</dt><dd>{{ targetText || 'not reported' }}</dd>
          <dt>Selection basis</dt><dd>{{ overridden ? basisLabel('manual_override') : basisLabel(chosen.selectionBasis ?? set?.selectionBasis, chosen.policyVersion ?? set?.policyVersion, chosen.relatedExperiments ?? set?.relatedExperiments) }}</dd>
          <dt v-if="chosen.approvalStatus">Approval</dt><dd v-if="chosen.approvalStatus">{{ humanize(chosen.approvalStatus) }}</dd>
          <dt v-if="chosen.executionStatus">Execution</dt>
          <dd v-if="chosen.executionStatus">{{ humanize(chosen.executionStatus) }}<span v-if="chosen.dryRun" class="badge tone-warn" style="margin-left: 6px">GitHub dry-run preview</span>
            <a v-if="chosen.executionReference?.startsWith('http')" :href="chosen.executionReference" target="_blank" rel="noopener noreferrer"> · {{ chosen.executionReference }}</a></dd>
        </dl>
        <details v-if="change?.diff">
          <summary>Proposed change (diff)</summary>
          <pre>{{ change.diff }}</pre>
        </details>
        <GuardVerdict :guard="chosen.guard" ask-later />
        <p v-if="chosen.action === 'observe'" class="dim">Incident remains open. System monitors the next measurement window. No external mutation occurs.</p>
        <p v-else-if="canApprove" class="meta">Approving activates an experiment. The change is applied by you (or an adapter you select), and Profound Lift records exactly what was done before measuring the result.</p>

        <div v-if="mode === 'modify'" class="stack sm">
          <label class="stack sm"><span class="meta">Edit proposed change</span><textarea v-model="draft" rows="8" class="mono" /></label>
          <label class="stack sm"><span class="meta">Note (optional)</span><input v-model="note"></label>
          <div class="row"><button type="button" class="primary" :disabled="api.submitting.value" @click="submitModify">Submit modification</button><button type="button" @click="mode = 'idle'">Cancel</button></div>
        </div>
        <div v-else-if="mode === 'reject'" class="stack sm">
          <label class="stack sm"><span class="meta">Why this recommendation is rejected</span>
            <select v-model="reasonCode">
              <option v-for="[value, label] in REJECTION_REASONS" :key="value" :value="value">{{ label }}</option>
            </select>
          </label>
          <label class="stack sm"><span class="meta">Note (optional)</span><input v-model="note"></label>
          <div class="row"><button type="button" class="danger" :disabled="api.submitting.value" @click="submitReject">Confirm reject</button><button type="button" @click="mode = 'idle'">Cancel</button></div>
        </div>
        <div v-else class="row wrap">
          <button class="primary" type="button" :disabled="!canApprove || !gate.allowed && !gate.needsReason" :title="!gate.allowed && !gate.needsReason ? gate.message ?? undefined : canApprove ? undefined : incident.allowedActions?.find((x) => x.action === 'approve')?.reason ?? `Not available while incident is ${incidentStatusLabel(incident)}`" data-testid="approve-open" @click="approve">
            {{ chosen.action === 'observe' ? 'Approve observation' : 'Approve & Start Experiment' }}
          </button>
          <button type="button" :disabled="!canApprove || chosen.action === 'observe'" @click="mode = 'modify'">Modify</button>
          <button type="button" class="danger" :disabled="!canApprove" @click="mode = 'reject'">Reject</button>
        </div>
        <p v-if="localErr" class="tone-bad" role="alert">{{ localErr }}</p>
        <p v-if="done" class="tone-good" role="status">{{ done }}</p>
      </div>

      <section v-if="alternatives.length" aria-label="Candidate interventions">
        <h3>Candidate interventions</h3>
        <div class="stack sm" style="margin-top: 8px">
          <InterventionCandidate v-for="a in alternatives" :key="a.id" :candidate="a" :disabled="!canApprove" @choose="chosenId = $event.id" />
        </div>
        <p class="meta">Scores are policy scores, not probabilities.</p>
      </section>
      <ExpectedOutcomePanel :outcome="chosen?.expectedOutcome ?? incident.expectedOutcome" />
    </template>
  </section>
</template>
