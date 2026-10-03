<script setup lang="ts">
import type { MetricDelta } from '~/types'

const props = defineProps<{ metric: MetricDelta }>()
const m = computed(() => props.metric)
const tone = computed(() => (m.value.favorable === true ? 'good' : m.value.favorable === false ? 'bad' : 'muted'))
const hasPair = computed(() => m.value.before != null && m.value.after != null)
const deltaText = computed(() => formatDeltaValue(m.value.delta, m.value.unit))

const points = computed(() => {
  const t = m.value.trend
  if (!t || t.length < 2) return ''
  const min = Math.min(...t), max = Math.max(...t), span = max - min || 1
  return t.map((v, i) => `${(i / (t.length - 1)) * 90},${24 - ((v - min) / span) * 20}`).join(' ')
})

const strokeColor = computed(() => {
  if (tone.value === 'good') return '#10b981'
  if (tone.value === 'bad') return '#f43f5e'
  return '#94a3b8'
})
</script>

<template>
  <div class="card metric-card" data-testid="metric-card">
    <div class="metric-top">
      <span class="metric-label">{{ m.label }}</span>
    </div>
    <div class="metric-center">
      <div class="metric-value">
        <template v-if="hasPair">
          <span class="before">{{ formatValue(m.before, m.unit) }}</span>
          <span class="arrow">→</span>
          <span class="after">{{ formatValue(m.after, m.unit) }}</span>
        </template>
        <template v-else>
          <span class="single">{{ formatValue(m.value ?? m.after ?? m.before, m.unit) }}</span>
        </template>
      </div>
      <span v-if="deltaText" :class="['delta-badge', `tone-${tone}`]">
        {{ deltaText }}
      </span>
    </div>
    <div class="metric-sparkline" v-if="points">
      <svg width="100%" height="28" viewBox="0 0 90 28" preserveAspectRatio="none" role="img" :aria-label="metricTrendText(m.label, m.trend)">
        <defs>
          <linearGradient :id="`grad-${m.key || m.label}`" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" :stop-color="strokeColor" stop-opacity="0.3" />
            <stop offset="100%" :stop-color="strokeColor" stop-opacity="0.0" />
          </linearGradient>
        </defs>
        <polyline :points="points" fill="none" :stroke="strokeColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </div>
  </div>
</template>

<style scoped>
.metric-card {
  padding: 12px 14px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
  gap: 6px;
  position: relative;
  overflow: hidden;
}
.metric-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.metric-label {
  font-size: 12px;
  color: var(--text-dim);
  font-weight: 500;
}
.metric-center {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}
.metric-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  display: flex;
  align-items: baseline;
  gap: 4px;
  letter-spacing: -0.02em;
}
.metric-value .before {
  color: var(--text-dim);
}
.metric-value .arrow {
  font-size: 13px;
  color: var(--text-faint);
}
.metric-value .after {
  color: var(--text-primary);
}
.delta-badge {
  font-size: 12px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
}
.delta-badge.tone-bad {
  color: #f43f5e;
  background: rgba(244, 63, 94, 0.12);
}
.delta-badge.tone-good {
  color: #10b981;
  background: rgba(16, 185, 129, 0.12);
}
.delta-badge.tone-muted {
  color: var(--text-dim);
}
.metric-sparkline {
  margin-top: 4px;
  height: 28px;
}
</style>
