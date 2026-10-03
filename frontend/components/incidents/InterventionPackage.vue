<script setup lang="ts">
import type { ApiErrorInfo, InterventionCandidate } from '~/types'

// The intervention package issued after approval (manual executor, the default). AEO SRE decides and explains;
// a human applies the exact change and records it here. Never optimistic: the backend answer is re-fetched.
const props = defineProps<{ candidate: InterventionCandidate; incidentId: string }>()
const emit = defineEmits<{ recorded: [] }>()
const api = useApproveIntervention(() => props.incidentId)

const pkg = computed(() => props.candidate.package ?? null)
const pending = computed(() => props.candidate.manualExecutionPending === true)
const executedAt = ref(localNow())
const referenceUrl = ref('')
const note = ref('')
const different = ref(false)
const actualChange = ref('')
const err = ref<ApiErrorInfo | null>(null)
const ok = ref<string | null>(null)

function localNow(): string {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}

const futureTime = computed(() => !!executedAt.value && new Date(executedAt.value).getTime() > Date.now() + 60_000)
const needsActual = computed(() => different.value && !actualChange.value.trim())

async function submit() {
  err.value = null
  ok.value = null
  try {
    const out = await api.recordExecuted(props.candidate.id, {
      executedAt: executedAt.value ? new Date(executedAt.value).toISOString() : undefined,
      referenceUrl: referenceUrl.value.trim() || undefined,
      note: note.value.trim() || undefined,
      actualChange: different.value ? actualChange.value : undefined
    })
    ok.value = out === null ? 'Already recorded. Showing the current state from the backend.' : 'Execution recorded. The measurement window has started; waiting for backend state.'
    emit('recorded')
  } catch (e) { err.value = e as ApiErrorInfo }
}
</script>

<template>
  <section v-if="pkg" class="card stack sm" aria-label="Intervention package" data-testid="intervention-package">
    <div class="row spread wrap">
      <h2>Intervention package</h2>
      <StatusBadge :label="`Executor: ${executorLabel(candidate.executor)}`" tone="muted" />
    </div>
    <p class="dim">
      Approving activated an experiment. {{ pkg.requiresHumanStep === false ? 'This is an observation: no change is applied and nothing is required from you.' : 'Apply the change below yourself, then record when you did. Nothing was changed in any external system by AEO SRE.' }}
    </p>
    <dl class="kv">
      <dt>Target</dt><dd>{{ pkg.target?.url ?? candidate.executionTarget ?? (pkg.target?.paths ?? []).join(', ') ?? 'not reported' }}</dd>
      <dt v-if="pkg.target?.paths?.length">Page / path</dt><dd v-if="pkg.target?.paths?.length" class="mono">{{ pkg.target.paths.join(', ') }}</dd>
      <dt>Risk</dt><dd>{{ humanize(pkg.risk) }}</dd>
      <dt v-if="pkg.approvedBy">Approved by</dt><dd v-if="pkg.approvedBy">{{ pkg.approvedBy }}<span v-if="pkg.modifiedByHuman" class="meta"> (modified)</span></dd>
      <dt>Rollback</dt><dd>{{ pkg.rollback ?? 'not reported' }}</dd>
      <dt>Observation window</dt><dd>{{ candidate.observationWindow ?? 'not reported' }}</dd>
    </dl>

    <ol v-if="pkg.steps.length" class="stack sm" data-testid="package-steps" style="margin: 0; padding-left: 20px">
      <li v-for="(s, i) in pkg.steps" :key="i">{{ s }}</li>
    </ol>

    <template v-for="c in pkg.changes" :key="c.path">
      <h3>Exact text to apply: <span class="mono">{{ c.path }}</span></h3>
      <p v-if="!c.currentContentKnown" class="meta">Current content of this page was not available to AEO SRE; the diff shows the proposed text as an addition.</p>
      <pre v-if="c.diff" data-testid="package-diff">{{ c.diff }}</pre>
      <details><summary>Proposed text (copy)</summary><pre data-testid="package-text">{{ c.proposedText }}</pre></details>
    </template>
    <template v-if="pkg.manualTask">
      <h3>{{ pkg.manualTask.title ?? 'Task' }}</h3>
      <p v-if="pkg.manualTask.recipient" class="meta">Recipient: {{ pkg.manualTask.recipient }}</p>
      <pre v-if="pkg.manualTask.body">{{ pkg.manualTask.body }}</pre>
    </template>

    <details v-if="pkg.evidenceSummary.length">
      <summary>Evidence behind this ({{ pkg.evidenceSummary.length }})</summary>
      <ul style="margin: 0; padding-left: 18px">
        <li v-for="(e, i) in pkg.evidenceSummary" :key="i">{{ e.title }} <span class="meta">· {{ humanize(e.type) }} · {{ humanize(e.status) }}</span></li>
      </ul>
    </details>

    <form v-if="pending" class="stack sm" data-testid="mark-executed-form" @submit.prevent="submit">
      <h3>Mark as executed</h3>
      <label class="stack sm"><span class="meta">When did you apply it?</span><input v-model="executedAt" type="datetime-local" required data-testid="executed-at"></label>
      <p v-if="futureTime" class="tone-bad" role="alert">The time cannot be in the future.</p>
      <label class="stack sm"><span class="meta">Reference URL (optional): live page, ticket, commit</span><input v-model="referenceUrl" type="url" placeholder="https://"></label>
      <label class="stack sm"><span class="meta">Note (optional)</span><textarea v-model="note" rows="2" /></label>
      <label class="row"><input v-model="different" type="checkbox" data-testid="different-toggle"> <span>I changed something different from the proposal</span></label>
      <label v-if="different" class="stack sm"><span class="meta">Paste exactly what you applied. It is stored verbatim and flagged as a deviation.</span><textarea v-model="actualChange" rows="6" class="mono" data-testid="actual-change" /></label>
      <p v-if="err" class="tone-bad" role="alert">Could not record: {{ err.message }} {{ err.detail }}</p>
      <p v-if="ok" class="tone-good" role="status">{{ ok }}</p>
      <div class="row"><button class="primary" type="submit" :disabled="api.submitting.value || futureTime || needsActual" data-testid="mark-executed-submit">{{ api.submitting.value ? 'Recording…' : 'Mark as executed' }}</button></div>
    </form>
    <p v-else-if="candidate.executionStatus === 'succeeded'" class="tone-good" role="status">
      Executed<template v-if="candidate.executedBy"> by {{ candidate.executedBy }}</template><template v-if="candidate.executedAt"> at {{ absoluteTime(candidate.executedAt) }}</template>.
      <span v-if="candidate.deviation" class="badge tone-warn" style="margin-left: 6px">Deviation from proposal</span>
      <a v-if="candidate.executionReference?.startsWith('http')" :href="candidate.executionReference" target="_blank" rel="noopener noreferrer"> · {{ candidate.executionReference }}</a>
    </p>
    <pre v-if="candidate.deviation && candidate.actualChange" data-testid="actual-change-shown">{{ candidate.actualChange }}</pre>
  </section>
</template>
