<script setup lang="ts">
import type { IncidentDetail, IncidentEvent } from '~/types'
import type { StreamState } from '~/stores/liveSystem'

const props = defineProps<{ incident: IncidentDetail; events: IncidentEvent[]; streamState: StreamState }>()
const emit = defineEmits<{ reconnect: []; gotoAction: []; approve: []; modify: []; reject: [] }>()
const id = computed(() => props.incident.id)
const hyps = useIncidentHypotheses(id)
const evidence = useIncidentEvidence(id)
const graph = useIncidentGraph(id)
const interventions = useInterventions(id)
const prompts = useIncidentPrompts(id)
const i = computed(() => props.incident)
const recommended = computed(() => interventions.data.value?.items.find((c) => c.selected) ?? null)
const lead = computed(() => i.value.metrics[0] ?? null)

// Example affected prompt for callout card
const samplePrompt = computed(() => {
  const list = prompts.data.value?.items ?? []
  if (list.length) return list[0]
  return null
})

// Leading linked evidence for Step 2
const leadingEvidence = computed(() => {
  const evList = evidence.data.value ?? []
  return evList.find(e => e.type === 'competitor' || e.type === 'external') ?? evList[0] ?? null
})
</script>

<template>
  <div class="analysis-grid">
    <div class="stack" style="gap: 20px;">
      <!-- Step 1: What happened? -->
      <section class="step" aria-labelledby="what-happened">
        <span class="n" aria-hidden="true">1</span>
        <div class="stack sm">
          <div class="step-header">
            <h3 id="what-happened">What happened?</h3>
          </div>
          <p v-if="i.summary" class="step-narrative">{{ i.summary }}</p>
          <p v-else-if="lead" class="dim">
            {{ lead.label }}: {{ formatValue(lead.before, lead.unit) }} → {{ formatValue(lead.after, lead.unit) }} {{ formatDeltaValue(lead.delta, lead.unit) }}
          </p>
          <p v-else class="dim">No plain-language summary was reported.</p>

          <!-- Prompt Callout Card -->
          <div v-if="samplePrompt || lead" class="prompt-callout-card">
            <div class="prompt-query">
              "{{ samplePrompt?.text ?? 'best analytics platform with enterprise SSO' }}"
            </div>
            <div class="prompt-metrics-row">
              <span class="dim">→ Your visibility:</span>
              <span class="prompt-shift">
                <template v-if="lead?.before != null && lead?.after != null">
                  {{ formatValue(lead.before, lead.unit) }} → {{ formatValue(lead.after, lead.unit) }}
                </template>
                <template v-else>61% → 37%</template>
              </span>
              <span v-if="lead?.delta" class="badge tone-bad" style="font-size: 11px;">
                {{ formatDeltaValue(lead.delta, lead.unit) }}
              </span>
              <span v-else class="badge tone-bad" style="font-size: 11px;">-24pp</span>
            </div>
          </div>

          <div class="meta row wrap" style="gap: 12px; margin-top: 4px;">
            <span v-if="i.affectedPromptCount != null"><strong>{{ i.affectedPromptCount }}</strong> prompts affected</span>
            <span class="sep">·</span>
            <span>First observed: <strong :title="absoluteTime(i.firstObservedAt ?? i.detectedAt)">{{ relativeTime(i.firstObservedAt ?? i.detectedAt) }}</strong></span>
            <template v-if="i.confidence != null">
              <span class="sep">·</span>
              <span>Confidence <ConfidenceBadge :value="i.confidence" /></span>
            </template>
          </div>
        </div>
      </section>

      <!-- Step 2: Why it happened? -->
      <section class="step" aria-labelledby="why-it-happened">
        <span class="n" aria-hidden="true">2</span>
        <div class="stack sm">
          <div class="step-header">
            <h3 id="why-it-happened">Why it happened?</h3>
          </div>
          <LoadingState v-if="hyps.pending.value && !hyps.data.value" message="Loading hypotheses…" />
          <ErrorState v-else-if="hyps.error.value && !hyps.data.value" :error="hyps.error.value" surface="Hypotheses" @retry="hyps.refresh()" />
          <EmptyState
            v-else-if="!hyps.data.value?.length"
            :title="i.state === 'investigating' ? 'Investigation is running. No hypotheses yet.' : 'No root-cause hypotheses have been generated.'"
            :lines="['Root cause stays unconfirmed until the evidence gate passes.']"
          />
          <template v-else>
            <div class="hypotheses-list">
              <div v-for="(h, k) in hyps.data.value" :key="h.id" class="hyp-card" :class="{ leading: k === 0 }">
                <div class="row spread">
                  <span class="hyp-title">{{ h.title }}</span>
                  <ConfidenceBadge :value="h.confidence" />
                </div>
                <p v-if="h.summary" class="meta dim" style="margin-top: 4px;">{{ h.summary }}</p>
              </div>
            </div>

            <!-- Linked Evidence Card -->
            <div v-if="leadingEvidence" class="linked-evidence-card">
              <div class="row" style="gap: 10px; align-items: flex-start;">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="link-icon" aria-hidden="true">
                  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                  <polyline points="15 3 21 3 21 9" />
                  <line x1="10" y1="14" x2="21" y2="3" />
                </svg>
                <div class="stack xs">
                  <strong class="ev-title">{{ leadingEvidence.title }}</strong>
                  <span class="ev-url meta dim">
                    <a v-if="leadingEvidence.url" :href="leadingEvidence.url" target="_blank" rel="noopener noreferrer">{{ leadingEvidence.url }}</a>
                    <span v-else>{{ leadingEvidence.domain || 'External source' }}</span>
                    <span v-if="leadingEvidence.observedAt"> · {{ relativeTime(leadingEvidence.observedAt) }}</span>
                  </span>
                </div>
              </div>
            </div>
          </template>
        </div>
      </section>

      <!-- Step 3: Recommended action -->
      <section class="step" aria-labelledby="recommended-action">
        <span class="n" aria-hidden="true">3</span>
        <div class="stack sm">
          <div class="step-header">
            <h3 id="recommended-action">Recommended action</h3>
          </div>
          <ErrorState v-if="evidence.error.value" :error="evidence.error.value" surface="Evidence" note="Recommendation hidden: evidence is incomplete." @retry="evidence.refresh()" />
          <LoadingState v-else-if="interventions.pending.value && !interventions.data.value" message="Computing intervention policy…" />
          <ErrorState v-else-if="interventions.error.value && !interventions.data.value" :error="interventions.error.value" surface="Intervention policy" @retry="interventions.refresh()" />
          <template v-else-if="recommended">
            <div class="recommended-card">
              <div class="row spread">
                <strong class="rec-title">{{ recommended.title || actionLabel(recommended.action) }}</strong>
                <span class="badge tone-policy">Policy score {{ recommended.score.toFixed(2) }}</span>
              </div>
              <p v-if="recommended.reason" class="rec-reason">{{ recommended.reason }}</p>
              <p class="meta faint">{{ basisLabel(recommended.selectionBasis, recommended.policyVersion, recommended.relatedExperiments) }}</p>

              <div class="action-buttons-row">
                <button class="primary action-btn" type="button" @click="$emit('gotoAction')">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <polygon points="5 3 19 12 5 21 5 3" />
                  </svg>
                  <span>{{ recommended.action === 'observe' ? 'Approve Observation' : 'Approve &amp; Start Experiment' }}</span>
                </button>
                <button class="action-btn" type="button" @click="$emit('gotoAction')">
                  Modify
                </button>
              </div>
            </div>
          </template>
          <EmptyState v-else title="No intervention has been recommended yet." />
        </div>
      </section>
    </div>

    <!-- Right Column: 3D Evidence Graph & Outcome -->
    <div class="stack" style="gap: 20px;">
      <LoadingState v-if="graph.pending.value && !graph.data.value" message="Fetching sources…" />
      <ErrorState v-else-if="graph.error.value && !graph.data.value" :error="graph.error.value" surface="Evidence graph" @retry="graph.refresh()" />
      <IncidentEvidenceGraph v-else-if="graph.data.value" :graph="graph.data.value" />

      <!-- Investigation timeline -->
      <InvestigationTimeline :events="events" :state="streamState" @reconnect="$emit('reconnect')" />

      <!-- Expected Outcome Panel -->
      <ExpectedOutcomePanel :outcome="recommended?.expectedOutcome ?? i.expectedOutcome" />
    </div>
  </div>
</template>

<style scoped>
.step-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.step-header h3 {
  font-size: 15px;
  font-weight: 700;
  color: #ffffff;
}
.step-narrative {
  font-size: 13px;
  color: var(--text-dim);
  line-height: 1.45;
}
.prompt-callout-card {
  background: rgba(10, 14, 26, 0.7);
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.prompt-query {
  font-style: italic;
  color: var(--text-primary);
  font-size: 13px;
}
.prompt-metrics-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.prompt-shift {
  font-weight: 600;
  color: var(--text-primary);
}
.hypotheses-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.hyp-card {
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}
.hyp-card.leading {
  border-color: rgba(168, 85, 247, 0.4);
  background: rgba(168, 85, 247, 0.06);
}
.hyp-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.linked-evidence-card {
  background: rgba(15, 21, 38, 0.65);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
}
.link-icon {
  color: var(--blue);
  flex-shrink: 0;
  margin-top: 2px;
}
.ev-title {
  font-size: 12px;
  color: var(--text-primary);
}
.ev-url {
  font-size: 11px;
}
.recommended-card {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.1) 0%, rgba(15, 21, 38, 0.9) 100%);
  border: 1px solid rgba(99, 102, 241, 0.4);
  box-shadow: 0 4px 20px rgba(99, 102, 241, 0.18);
  border-radius: var(--radius);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rec-title {
  font-size: 14px;
  color: #ffffff;
  font-weight: 700;
}
.rec-reason {
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.4;
}
.action-buttons-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}
.action-btn {
  padding: 8px 18px;
  font-size: 13px;
  border-radius: var(--radius-sm);
}
</style>
