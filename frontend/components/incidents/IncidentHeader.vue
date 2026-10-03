<script setup lang="ts">
import type { IncidentDetail } from '~/types'

const props = defineProps<{ incident: IncidentDetail; cta: Cta; busy?: boolean }>()
defineEmits<{ cta: [kind: CtaKind] }>()
const i = computed(() => props.incident)

const provenanceLabel = computed(() => {
  const ctx = i.value.context
  const detection = Array.isArray(ctx?.detection) ? ctx.detection : []
  const sources = detection
    .map(row => (row && typeof row === 'object' ? String((row as { source?: unknown }).source ?? '') : ''))
    .filter(Boolean)
  const explicit = typeof ctx?.provenance === 'string' ? ctx.provenance : ''
  const label = explicit || inferProvenance(sources)
  if (label === 'live') return 'Live Profound'
  if (label === 'test_fixture') return 'Test fixture'
  if (label === 'historical_replay') return 'Historical replay'
  if (label === 'mixed') return 'Mixed sources'
  return ''
})

function inferProvenance(sources: string[]): string {
  if (!sources.length) return ''
  if (sources.every(s => s === 'dev_fixture' || s.startsWith('dev_fixture'))) return 'test_fixture'
  if (sources.every(s => s === 'profound' || s.startsWith('profound'))) return 'live'
  return 'mixed'
}
</script>

<template>
  <header class="incident-header stack sm">
    <div class="row spread wrap">
      <div class="row wrap" style="gap: 12px; align-items: center;">
        <div class="header-icon" :class="`sev-${i.severity}`">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
        </div>
        <div class="stack xs">
          <div class="row wrap" style="gap: 8px; align-items: center;">
            <h2 class="incident-title" data-testid="incident-title">{{ i.title }}</h2>
            <SeverityBadge :severity="i.severity" />
            <span v-if="i.number != null" class="id-pill">#{{ i.number }}</span>
            <span v-if="provenanceLabel" class="pill-provenance" :class="provenanceLabel === 'Live Profound' ? 'live' : 'test'" data-testid="provenance-badge">
              {{ provenanceLabel }}
            </span>
          </div>
          <div class="meta-row">
            <span :title="absoluteTime(i.detectedAt)">Detected {{ relativeTime(i.detectedAt) }}</span>
            <span class="sep">·</span>
            <span class="status-text">{{ incidentStatusLabel(i) }}</span>
            <span v-if="cta.hint" class="dim"> · {{ cta.hint }}</span>
          </div>
        </div>
      </div>

      <div class="row" style="gap: 10px;">
        <NuxtLink v-if="i.experimentId" :to="`/experiments/${i.experimentId}`" class="button-link">View experiment →</NuxtLink>
        <button
          v-if="cta.kind !== 'none' || cta.disabled"
          :class="['cta-button', { primary: !cta.disabled }]"
          type="button"
          :disabled="cta.disabled || busy"
          :title="cta.hint"
          data-testid="primary-cta"
          @click="$emit('cta', cta.kind)"
        >
          <svg v-if="cta.kind === 'investigate'" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
          <span>{{ busy ? 'Working…' : cta.label }}</span>
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.incident-header {
  padding: 6px 0;
}
.header-icon {
  width: 42px;
  height: 42px;
  border-radius: var(--radius);
  background: rgba(15, 21, 38, 0.9);
  border: 1px solid var(--border);
  display: grid;
  place-items: center;
  color: var(--text-dim);
  flex-shrink: 0;
}
.header-icon.sev-critical {
  color: #f43f5e;
  border-color: rgba(244, 63, 94, 0.35);
  background: rgba(244, 63, 94, 0.12);
  box-shadow: 0 0 16px rgba(244, 63, 94, 0.25);
}
.header-icon.sev-high {
  color: #f97316;
  border-color: rgba(249, 115, 22, 0.35);
  background: rgba(249, 115, 22, 0.12);
}
.incident-title {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #ffffff;
}
.id-pill {
  padding: 2px 7px;
  border-radius: 4px;
  background: rgba(148, 163, 184, 0.1);
  border: 1px solid rgba(148, 163, 184, 0.2);
  color: var(--text-dim);
  font-size: 11px;
  font-weight: 600;
}
.meta-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-dim);
}
.status-text {
  color: var(--blue);
  font-weight: 500;
}
.sep {
  color: var(--text-faint);
}
.cta-button {
  padding: 8px 16px;
  font-size: 13px;
  border-radius: var(--radius-sm);
}
.button-link {
  font-size: 12px;
  color: var(--blue);
  padding: 6px 10px;
}
.button-link:hover {
  text-decoration: underline;
}
</style>
