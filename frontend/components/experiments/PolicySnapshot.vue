<script setup lang="ts">
import type { ExperimentDetail } from '~/types'

const props = defineProps<{ experiment: ExperimentDetail }>()
const e = computed(() => props.experiment)

interface ActionScore {
  action: string
  label: string
  score: number
  selected?: boolean
}

const actionScores = computed<ActionScore[]>(() => {
  const alts = e.value.alternatives ?? []
  if (alts.length) {
    return alts.map(a => ({
      action: a.action,
      label: a.title ?? actionLabel(a.action),
      score: a.score,
      selected: a.action === e.value.action
    }))
  }

  return [] // alternatives not recorded: render unavailable, never invented scores
})

function getScoreWidth(score: number): string {
  const maxScore = Math.max(0.5, ...actionScores.value.map(s => s.score))
  return `${Math.min(100, Math.max(10, (score / maxScore) * 100))}%`
}
</script>

<template>
  <section class="card stack sm policy-card" aria-label="Policy learning information" data-testid="policy-snapshot">
    <div class="row spread">
      <div class="row" style="gap: 8px;">
        <h3>Policy Learning</h3>
        <span class="meta faint">Adaptive selection</span>
      </div>
      <span v-if="e.policyVersion" class="badge tone-policy">Version {{ e.policyVersion }}</span>
    </div>

    <div class="policy-sub meta dim">
      {{ e.relatedExperiments != null ? `${e.relatedExperiments} rewarded experiments` : 'Rewarded experiment count unavailable' }}
    </div>

    <!-- Candidate action score bars -->
    <p v-if="!actionScores.length" class="dim meta" data-testid="scores-unavailable">Unavailable: no per-action scores were recorded for this decision.</p>
    <div v-else class="scores-list">
      <div
        v-for="item in actionScores"
        :key="item.action"
        class="score-item"
        :class="{ selected: item.selected }"
      >
        <div class="score-header">
          <span class="score-label">{{ item.label }}</span>
          <span class="score-val">{{ item.score.toFixed(2) }}</span>
        </div>
        <div class="score-track">
          <div
            class="score-fill"
            :class="{ active: item.selected }"
            :style="{ width: getScoreWidth(item.score) }"
          />
        </div>
      </div>
    </div>

    <div class="selected-note meta">
      Selected: <strong class="tone-policy">{{ e.actionTitle ?? actionLabel(e.action) }}</strong> for this context
    </div>

    <!-- Details fold for complete metadata -->
    <details class="policy-details">
      <summary class="meta">View policy parameters</summary>
      <dl class="kv" style="margin-top: 8px;">
        <dt>Selection basis</dt><dd>{{ basisLabel(e.selectionBasis, e.policyVersion, e.relatedExperiments) }}</dd>
        <dt>Selection probability</dt><dd>{{ e.policyProbability != null ? e.policyProbability.toFixed(2) : 'Unavailable' }}</dd>
        <dt>Cold start</dt><dd>{{ e.coldStart == null ? 'Unavailable' : e.coldStart ? 'Yes (Exploring)' : 'No (Calibrated)' }}</dd>
      </dl>
    </details>
  </section>
</template>

<style scoped>
.policy-card {
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.policy-sub {
  font-size: 11px;
}
.scores-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 4px 0;
}
.score-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.score-header {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}
.score-label {
  color: var(--text-dim);
}
.score-item.selected .score-label {
  color: #ffffff;
  font-weight: 600;
}
.score-val {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 11px;
  color: var(--text-faint);
}
.score-item.selected .score-val {
  color: #c084fc;
  font-weight: 700;
}
.score-track {
  height: 6px;
  background: rgba(10, 14, 26, 0.7);
  border-radius: 3px;
  overflow: hidden;
}
.score-fill {
  height: 100%;
  background: rgba(168, 85, 247, 0.35);
  border-radius: 3px;
}
.score-fill.active {
  background: linear-gradient(90deg, #9333ea 0%, #a855f7 100%);
  box-shadow: 0 0 8px rgba(168, 85, 247, 0.5);
}
.selected-note {
  font-size: 11px;
  color: var(--text-dim);
  padding: 6px 10px;
  background: rgba(168, 85, 247, 0.08);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(168, 85, 247, 0.2);
}
.policy-details summary {
  cursor: pointer;
  color: var(--text-faint);
  font-size: 11px;
}
</style>
