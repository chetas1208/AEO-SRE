<script setup lang="ts">
import type { MetricDelta } from '~/types'

const props = defineProps<{
  before?: MetricDelta[] | Record<string, number> | null
  after?: MetricDelta[] | Record<string, number> | null
  outcome?: MetricDelta[] | null
  awaiting?: boolean
  dryRun?: boolean | null
}>()

const rows = computed<MetricDelta[]>(() => {
  if (props.outcome?.length) return props.outcome
  const asMap = (v: typeof props.before) =>
    Array.isArray(v)
      ? new Map(v.map((m) => [m.key ?? m.label, m]))
      : new Map(Object.entries(v ?? {}).map(([k, val]) => [k, { label: humanize(k), key: k, value: val } as MetricDelta]))
  const b = asMap(props.before), a = asMap(props.after)
  const keys = [...new Set([...b.keys(), ...a.keys()])]

  return keys.map((k) => {
    const bm = b.get(k), am = a.get(k)
    const bv = bm?.after ?? bm?.value ?? null
    const av = am?.after ?? am?.value ?? null
    return {
      label: (bm ?? am)!.label,
      key: k,
      before: bv,
      after: av,
      unit: (bm ?? am)!.unit ?? null,
      delta: am?.delta ?? (bv != null && av != null ? av - bv : null),
      favorable: am?.favorable ?? null
    }
  })
})

function getBarWidth(val: number | null | undefined, unit: string | null | undefined): string {
  if (val == null) return '0%'
  if (unit === 'fraction' || unit === '%') {
    const pct = unit === 'fraction' ? val * 100 : val
    return `${Math.min(100, Math.max(4, pct))}%`
  }
  // For prompt volume or raw counts
  const norm = Math.min(100, Math.max(6, (val / 2000) * 100))
  return `${norm}%`
}
</script>

<template>
  <section class="card stack sm" aria-label="Before and after primary metrics" data-testid="experiment-outcome">
    <div class="row spread">
      <h3>Before → After (Primary Metrics)</h3>
      <div class="legend-row">
        <span class="legend-item"><span class="legend-dot before" /> Before</span>
        <span class="legend-item"><span class="legend-dot after" /> After</span>
      </div>
    </div>

    <p v-if="!rows.length" class="dim" data-testid="metrics-unavailable">Unavailable: no before/after metrics were recorded for this experiment.</p>
    <div v-else class="bars-container">
      <div v-for="r in rows" :key="r.key ?? r.label" class="metric-bar-group">
        <div class="bar-header">
          <span class="metric-name">{{ r.label }}</span>
          <div class="metric-vals">
            <span class="val-before">{{ formatValue(r.before, r.unit) }}</span>
            <span class="val-arrow">→</span>
            <span class="val-after">{{ r.after != null ? formatValue(r.after, r.unit) : '—' }}</span>
          </div>
        </div>
        <div class="bar-track">
          <!-- Before bar -->
          <div class="bar-fill before" :style="{ width: getBarWidth(r.before, r.unit) }" />
          <!-- After bar (if available) -->
          <div v-if="r.after != null" class="bar-fill after" :style="{ width: getBarWidth(r.after, r.unit) }" />
        </div>
      </div>
    </div>

    <p v-if="dryRun" class="tone-warn meta" style="margin-top: 6px;">
      GitHub dry-run preview: nothing was changed, so no post-intervention measurement will be recorded.
    </p>
    <p v-else-if="awaiting" class="tone-warn meta" style="margin-top: 6px;">
      Awaiting post-intervention observation. No result is shown until measured.
    </p>

    <!-- Accessible hidden table for screen readers and tests -->
    <details class="sr-only-details">
      <summary class="sr-only">Detailed table view</summary>
      <table>
        <thead>
          <tr><th scope="col">Metric</th><th scope="col">Before</th><th scope="col">After</th><th scope="col">Change</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.key ?? r.label">
            <th scope="row">{{ r.label }}</th>
            <td>{{ formatValue(r.before, r.unit) }}</td>
            <td>{{ r.after != null ? formatValue(r.after, r.unit) : '—' }}</td>
            <td>{{ r.delta != null ? formatDeltaValue(r.delta, r.unit) : '—' }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </section>
</template>

<style scoped>
.legend-row {
  display: flex;
  gap: 12px;
  font-size: 11px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-dim);
}
.legend-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.legend-dot.before {
  background: #818cf8;
}
.legend-dot.after {
  background: #38bdf8;
}

.bars-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 4px;
}
.metric-bar-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bar-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.metric-name {
  font-size: 12px;
  color: var(--text-dim);
  font-weight: 500;
}
.metric-vals {
  display: flex;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
}
.val-before {
  color: var(--text-dim);
}
.val-arrow {
  color: var(--text-faint);
  font-weight: 400;
}
.val-after {
  color: var(--text-primary);
}
.bar-track {
  height: 8px;
  background: rgba(10, 14, 26, 0.7);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  overflow: hidden;
  position: relative;
  display: flex;
}
.bar-fill.before {
  height: 100%;
  background: linear-gradient(90deg, #4f46e5 0%, #818cf8 100%);
  border-radius: 4px;
}
.bar-fill.after {
  height: 100%;
  background: linear-gradient(90deg, #0284c7 0%, #38bdf8 100%);
  border-radius: 4px;
}
</style>
