<script setup lang="ts">
import type { ExperimentSummaryRow } from '~/types'

const props = defineProps<{
  experiment: ExperimentSummaryRow
  selected?: boolean
}>()

const e = computed(() => props.experiment)

const code = computed(() => {
  return e.value.code ?? `EXP-${e.value.id.slice(0, 4)}`
})

const provenance = computed<'live' | 'test' | 'replay'>(() => {
  const s = (e.value.source ?? '').toLowerCase()
  if (s.includes('test') || s.includes('fixture')) return 'test'
  if (s.includes('replay')) return 'replay'
  if (s.includes('live') || s.includes('profound')) return 'live'
  return 'test'
})
</script>

<template>
  <NuxtLink
    :to="`/experiments/${e.id}`"
    :class="['card exp-card', { selected }]"
    :aria-current="selected ? 'true' : undefined"
  >
    <div class="exp-card-header">
      <div class="row" style="gap: 6px;">
        <span class="exp-code">{{ code }}</span>
        <span class="pill-provenance" :class="provenance">{{ provenance.toUpperCase() }}</span>
      </div>
      <StatusBadge :label="e.displayStatus ?? experimentLabel(e.status)" :tone="experimentTone(e.status)" />
    </div>

    <div class="exp-title-block">
      <strong class="exp-title">{{ e.incidentTitle ?? 'Intervention Experiment' }}</strong>
      <span class="exp-action">{{ e.actionTitle ?? actionLabel(e.action) }}</span>
    </div>

    <div class="exp-card-footer">
      <span class="exp-date">{{ shortDate(e.startedAt) }}</span>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="chevron" aria-hidden="true">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </div>
  </NuxtLink>
</template>

<style scoped>
.exp-card {
  padding: 12px 14px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: all var(--motion-normal) var(--ease-calm);
  position: relative;
  text-decoration: none;
  color: inherit;
}
.exp-card:hover {
  border-color: var(--border-strong);
  background: var(--surface-hover);
  transform: translateX(2px);
}
.exp-card.selected {
  border-color: var(--primary);
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.12) 0%, rgba(15, 21, 38, 0.95) 100%);
  box-shadow: 0 0 0 1px var(--primary), 0 8px 24px rgba(99, 102, 241, 0.25);
}
.exp-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.exp-code {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-dim);
}
.exp-title-block {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.exp-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.3;
}
.exp-action {
  font-size: 11px;
  color: var(--blue);
}
.exp-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 4px;
}
.exp-date {
  font-size: 11px;
  color: var(--text-faint);
}
.chevron {
  color: var(--text-faint);
}
</style>
