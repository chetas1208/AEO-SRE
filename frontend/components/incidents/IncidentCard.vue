<script setup lang="ts">
import type { IncidentSummary } from '~/types'

const props = defineProps<{ incident: IncidentSummary; selected?: boolean }>()
const i = computed(() => props.incident)
const delta = computed(() => {
  const d = i.value.primaryDelta
  if (!d) return null
  const t = formatDeltaValue(d.delta, d.unit)
  return t ? `${t} ${d.label}` : d.label
})

const tone = computed(() => {
  if (i.value.severity === 'critical') return 'bad'
  if (i.value.severity === 'high') return 'warn'
  if (i.value.severity === 'medium') return 'warn'
  return 'good'
})

const points = computed(() => {
  const t = i.value.trend
  if (!t || t.length < 2) return ''
  const min = Math.min(...t), max = Math.max(...t), span = max - min || 1
  return t.map((v, k) => `${(k / (t.length - 1)) * 64},${20 - ((v - min) / span) * 16}`).join(' ')
})

const strokeColor = computed(() => {
  if (tone.value === 'bad') return '#f43f5e'
  if (tone.value === 'warn') return '#f59e0b'
  return '#10b981'
})
</script>

<template>
  <NuxtLink
    :to="`/incidents/${i.id}`"
    :class="['card incident-card', `sev-${i.severity}`, { selected }]"
    :aria-current="selected ? 'true' : undefined"
    data-testid="incident-card"
  >
    <div class="card-inner">
      <div class="row spread" style="align-items: flex-start;">
        <div class="row" style="gap: 10px; align-items: flex-start;">
          <div class="cat-icon" :class="`sev-${i.severity}`">
            <svg v-if="i.title.toLowerCase().includes('sso') || i.title.toLowerCase().includes('security') || i.topic?.includes('security')" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            <svg v-else-if="i.title.toLowerCase().includes('competitor') || i.title.toLowerCase().includes('citation')" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6" />
              <path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18" />
              <path d="M4 22h16" />
              <path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22" />
              <path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22" />
              <path d="M18 2H6v7a6 6 0 0 0 12 0V2Z" />
            </svg>
            <svg v-else-if="i.title.toLowerCase().includes('capability') || i.title.toLowerCase().includes('fact') || i.title.toLowerCase().includes('product')" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
            <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
              <polyline points="17 6 23 6 23 12" />
            </svg>
          </div>
          <div class="stack xs">
            <strong class="incident-title">{{ i.title }}</strong>
            <p v-if="delta" class="delta-text">{{ delta }}</p>
            <p v-else-if="i.contextLabel || i.topic" class="meta-topic">{{ i.contextLabel ?? i.topic }}</p>
          </div>
        </div>
        <SeverityBadge :severity="i.severity" />
      </div>

      <div class="row spread" style="margin-top: 10px; align-items: flex-end;">
        <span class="meta-detected">
          <DataFreshness :at="i.detectedAt" />
        </span>
        <svg v-if="points" width="64" height="24" viewBox="0 0 64 24" role="img" :aria-label="metricTrendText(i.primaryDelta?.label ?? 'Metric', i.trend)">
          <polyline :points="points" fill="none" :stroke="strokeColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </div>
    </div>
  </NuxtLink>
</template>

<style scoped>
.incident-card {
  padding: 12px 14px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: var(--radius);
  position: relative;
  transition: all var(--motion-normal) var(--ease-calm);
}
.incident-card.selected {
  border-color: var(--primary);
  border-left-color: var(--primary);
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.12) 0%, rgba(15, 21, 38, 0.95) 100%);
  box-shadow: 0 0 0 1px var(--primary), 0 8px 24px rgba(99, 102, 241, 0.25);
}
.incident-card.sev-critical { border-left-color: var(--bad); }
.incident-card.sev-high { border-left-color: #f97316; }
.incident-card.sev-medium { border-left-color: var(--warn); }
.incident-card.sev-low { border-left-color: var(--good); }

.cat-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: var(--bg-1);
  border: 1px solid var(--border);
  display: grid;
  place-items: center;
  color: var(--text-dim);
  flex-shrink: 0;
}
.cat-icon.sev-critical {
  color: var(--bad);
  border-color: rgba(244, 63, 94, 0.3);
  background: rgba(244, 63, 94, 0.1);
}
.cat-icon.sev-high {
  color: #f97316;
  border-color: rgba(249, 115, 22, 0.3);
  background: rgba(249, 115, 22, 0.1);
}
.cat-icon.sev-medium {
  color: var(--warn);
  border-color: rgba(245, 158, 11, 0.3);
  background: rgba(245, 158, 11, 0.1);
}

.incident-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.3;
}
.delta-text {
  font-size: 11px;
  color: var(--text-dim);
  line-height: 1.2;
}
.meta-topic {
  font-size: 11px;
  color: var(--text-faint);
}
.meta-detected {
  font-size: 11px;
  color: var(--text-faint);
}
</style>
