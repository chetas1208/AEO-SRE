<script setup lang="ts">
import type { ApiErrorInfo, InterventionCandidate } from '~/types'

const route = useRoute()
const id = computed(() => String(route.params.id))
const sel = useIncidentSelectionStore()
sel.select(id.value)

const incident = useIncident(id)
const interventions = useInterventions(id)
const prompts = useIncidentPrompts(id)
const evidence = useIncidentEvidence(id)

type Tab = 'analysis' | 'prompts' | 'evidence' | 'action'
const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'analysis', label: 'Analysis' }, { key: 'prompts', label: 'Affected Prompts' }, { key: 'evidence', label: 'Evidence' }, { key: 'action', label: 'Action' }
]
const tab = computed<Tab>({
  get: () => (TABS.some((t) => t.key === route.query.tab) ? (route.query.tab as Tab) : 'analysis'),
  set: (v) => { navigateTo({ query: { ...route.query, tab: v === 'analysis' ? undefined : v } }, { replace: true }) }
})

async function refreshAll() {
  await Promise.allSettled([
    incident.refresh(), interventions.refresh(), prompts.refresh(), evidence.refresh(),
    refreshNuxtData(`incident:${id.value}:hypotheses`), refreshNuxtData(`incident:${id.value}:graph`)
  ])
}
let debounce: ReturnType<typeof setTimeout> | undefined
const stream = useIncidentEvents(id, () => { clearTimeout(debounce); debounce = setTimeout(refreshAll, 800) })
onBeforeUnmount(() => clearTimeout(debounce))

const hasIntervention = computed(() => (interventions.data.value?.items.length ?? 0) > 0)
const cta = computed(() => (incident.data.value ? resolveCta(incident.data.value, hasIntervention.value) : primaryCta('', false)))
const busy = ref(false)
const actionMsg = ref<string | null>(null)
const actionErr = ref<ApiErrorInfo | null>(null)
const approvalTarget = ref<InterventionCandidate | null>(null)

async function onCta(kind: CtaKind) {
  actionMsg.value = null
  actionErr.value = null
  const items = interventions.data.value?.items ?? []
  const target = items.find((c) => c.id === cta.value.interventionId) ?? items.find((c) => c.selected) ?? null
  if (kind === 'mark_executed') { tab.value = 'action'; await nextTick(); document.querySelector('[data-testid="intervention-package"]')?.scrollIntoView({ block: 'start' }); return }
  if (kind === 'review') { tab.value = (TABS.find((t) => t.key === cta.value.targetTab)?.key ?? 'action'); return }
  if (kind === 'approve') {
    tab.value = 'action'
    approvalTarget.value = target
    return
  }
  busy.value = true
  try {
    if (kind === 'investigate') { await investigateIncident(id.value); actionMsg.value = 'Investigation requested. Waiting for backend progress.' }
    else if (kind === 'execute') {
      if (target) { await apiFetch(`/api/interventions/${encodeURIComponent(target.id)}/execute`, { method: 'POST', body: {} }); actionMsg.value = 'Experiment activation requested. Waiting for backend state.' }
    } else if (kind === 'resolve') { await resolveIncident(id.value); actionMsg.value = 'Resolve submitted.' }
    await refreshAll()
  } catch (e) {
    actionErr.value = e as ApiErrorInfo
  } finally {
    busy.value = false
  }
}

function onApprovalDone() { refreshAll() }
const count = (n: number | null | undefined) => (n != null ? ` ${n}` : '')
</script>

<template>
  <div class="stack" :key="id">
    <LoadingState v-if="incident.pending.value && !incident.data.value" message="Loading incident…" />
    <ErrorState v-else-if="incident.error.value && !incident.data.value" :error="incident.error.value" surface="Incident" @retry="incident.refresh()" />
    <template v-else-if="incident.data.value">
      <NuxtLink to="/incidents" class="meta back-link">← Back to queue</NuxtLink>
      <IncidentHeader :incident="incident.data.value" :cta="cta" :busy="busy" @cta="onCta" />
      <section v-if="incident.data.value.explanation" class="stack sm" data-testid="incident-explanation">
        <p><strong>What changed.</strong> {{ incident.data.value.explanation.whatChanged }}</p>
        <p><strong>Why it matters.</strong> {{ incident.data.value.explanation.whyItMatters }}</p>
        <p>
          <strong>Recommended action.</strong>
          {{ incident.data.value.explanation.recommendedAction || 'None selected yet' }}
        </p>
        <p v-if="incident.data.value.explanation.leadingHypothesis" class="meta">
          Leading hypothesis ({{ incident.data.value.explanation.hypothesisStatus || 'proposed' }}):
          {{ incident.data.value.explanation.leadingHypothesis }}
          <span v-if="incident.data.value.explanation.confidence != null">
            · heuristic confidence {{ incident.data.value.explanation.confidence }}
          </span>
        </p>
        <p v-else class="meta">No root cause has been proposed.</p>
        <ul v-if="incident.data.value.explanation.policyScores?.length" class="meta">
          <li v-for="score in incident.data.value.explanation.policyScores" :key="score.action">
            {{ score.action }} · {{ score.label || 'policy score' }} {{ score.policyScore ?? 'not scored' }}<span v-if="score.selected"> · selected</span>
          </li>
        </ul>
        <p v-if="incident.data.value.explanation.independenceNote" class="meta">
          {{ incident.data.value.explanation.independenceNote }}
        </p>
        <p v-if="incident.data.value.explanation.timings && Object.keys(incident.data.value.explanation.timings).length" class="meta">
          Investigation timing:
          <span v-for="(seconds, label) in incident.data.value.explanation.timings" :key="label">
            {{ label.replaceAll('_', ' ') }} {{ seconds == null ? 'not reached' : `${seconds}s` }}
          </span>
        </p>
        <p v-if="incident.data.value.explanation.verificationWindow" class="meta" data-testid="verification-window">
          Eligible for measurement after: {{ incident.data.value.explanation.verificationWindow }}
        </p>
        <p class="meta">{{ incident.data.value.explanation.note }}</p>
      </section>
      <p v-if="actionMsg" class="tone-good" role="status">{{ actionMsg }}</p>
      <ErrorState v-if="actionErr" :error="actionErr" surface="Action" />
      <IncidentMetricStrip :metrics="incident.data.value.metrics" />

      <div class="tabs" role="tablist" aria-label="Incident sections">
        <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" @click="tab = t.key">
          {{ t.label }}<span v-if="t.key === 'prompts'" class="faint">{{ count(prompts.data.value?.total ?? incident.data.value.affectedPromptCount) }}</span><span v-if="t.key === 'evidence'" class="faint">{{ count(evidence.data.value?.length ?? incident.data.value.evidenceCount) }}</span>
        </button>
      </div>

      <IncidentAnalysis v-if="tab === 'analysis'" :incident="incident.data.value" :events="stream.events.value" :stream-state="stream.state.value" @reconnect="stream.reconnect()" @goto-action="tab = 'action'" />

      <template v-else-if="tab === 'prompts'">
        <LoadingState v-if="prompts.pending.value && !prompts.data.value" message="Loading affected prompts…" />
        <ErrorState v-else-if="prompts.error.value && !prompts.data.value" :error="prompts.error.value" surface="Affected prompts" @retry="prompts.refresh()" />
        <IncidentPromptTable v-else :prompts="prompts.data.value?.items ?? []" :unavailable-reason="prompts.data.value?.unavailableReason" />
      </template>

      <template v-else-if="tab === 'evidence'">
        <LoadingState v-if="evidence.pending.value && !evidence.data.value" message="Fetching sources…" />
        <ErrorState v-else-if="evidence.error.value && !evidence.data.value" :error="evidence.error.value" surface="Evidence" @retry="evidence.refresh()" />
        <IncidentEvidenceList v-else :evidence="evidence.data.value ?? []" :incident-id="id" />
      </template>

      <IncidentActionPanel
        v-else
        :incident="incident.data.value" :set="interventions.data.value" :pending="interventions.pending.value" :error="interventions.error.value"
        :evidence-error="evidence.error.value" :evidence-pending="evidence.pending.value"
        @approve="approvalTarget = $event" @retry="interventions.refresh(); evidence.refresh()"
      />

      <ApprovalDialog :candidate="approvalTarget" :evidence="evidence.data.value" :incident-id="id" @close="approvalTarget = null" @done="onApprovalDone" />
    </template>
    <LoadingState v-else message="Loading incident…" />
  </div>
</template>
