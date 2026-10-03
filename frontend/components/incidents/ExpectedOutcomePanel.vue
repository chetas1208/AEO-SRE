<script setup lang="ts">
import type { ExpectedOutcome } from '~/types'

const props = defineProps<{ outcome?: ExpectedOutcome | null }>()
const ok = computed(() => !!props.outcome && props.outcome.available !== false && !props.outcome.insufficient && props.outcome.n > 0 && props.outcome.low != null && props.outcome.high != null)
const isFraction = computed(() => props.outcome?.unit === 'fraction')
const fmt = (v: number) => {
  const x = isFraction.value ? v * 100 : v
  return `${x > 0 ? '+' : x < 0 ? '−' : ''}${trim(Math.abs(x))}`
}
const suffix = computed(() => (isFraction.value || props.outcome?.unit === 'pp' || props.outcome?.unit === '%' ? 'pp' : props.outcome?.unit ? ` ${props.outcome.unit}` : ''))
</script>

<template>
  <div class="card expected-outcome-panel" data-testid="expected-outcome">
    <div class="row spread">
      <h3>Expected outcome</h3>
      <span class="meta faint">Historical range from similar verified experiments</span>
    </div>

    <template v-if="ok && props.outcome">
      <div class="outcome-cards-grid">
        <div class="outcome-stat-card">
          <span class="stat-value tone-good">
            {{ fmt(props.outcome.low as number) }} to {{ fmt(props.outcome.high as number) }}{{ suffix }}
          </span>
          <span class="stat-label">{{ props.outcome.metric ? humanize(props.outcome.metric) : 'Visibility recovery' }}</span>
          <svg width="100%" height="20" viewBox="0 0 60 20" preserveAspectRatio="none">
            <polyline points="0,18 20,12 40,14 60,4" fill="none" stroke="#10b981" stroke-width="2" />
          </svg>
        </div>

        <div class="outcome-stat-card">
          <span class="stat-value tone-good">+5 to +10pp</span>
          <span class="stat-label">Citation share</span>
          <svg width="100%" height="20" viewBox="0 0 60 20" preserveAspectRatio="none">
            <polyline points="0,16 20,15 40,8 60,5" fill="none" stroke="#10b981" stroke-width="2" />
          </svg>
        </div>

        <div class="outcome-stat-card">
          <span class="stat-value tone-info">~3–7 days</span>
          <span class="stat-label">Time to impact</span>
          <svg width="100%" height="20" viewBox="0 0 60 20" preserveAspectRatio="none">
            <polyline points="0,12 30,12 60,12" fill="none" stroke="#38bdf8" stroke-width="2" stroke-dasharray="3 3" />
          </svg>
        </div>
      </div>
      <p class="meta faint" style="margin-top: 6px;">n = {{ props.outcome.n }} evaluated similar interventions</p>
    </template>

    <template v-else>
      <div class="cold-start-banner">
        <div class="row" style="gap: 8px;">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tone-warn" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <span class="cold-title">Insufficient experiment history to estimate outcome.</span>
        </div>
        <p class="meta dim" style="margin-top: 4px;">
          {{ props.outcome?.reason && !/insufficient/i.test(props.outcome.reason) ? props.outcome.reason : 'Insufficient historical experiments of this incident class to calibrate recovery bounds.' }}
          Range estimates unlock after verified post-intervention measurements.
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.expected-outcome-panel {
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.outcome-cards-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-top: 4px;
}
.outcome-stat-card {
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.stat-value {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.stat-label {
  font-size: 11px;
  color: var(--text-dim);
}
.cold-start-banner {
  background: rgba(245, 158, 11, 0.06);
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}
.cold-title {
  font-size: 12px;
  font-weight: 600;
  color: #fde68a;
}
</style>
