<script setup lang="ts">
import type { EvidenceItem } from '~/types'

const props = defineProps<{ evidence: EvidenceItem[]; incidentId: string }>()
const ev = useEvidenceDetail(() => props.incidentId)
const ex = (e: EvidenceItem) => ev.detail.value[e.id]?.excerpt ?? e.excerpt
const SECTIONS = [
  { type: 'owned', label: 'Owned Sources' }, { type: 'competitor', label: 'Competitor Sources' },
  { type: 'external', label: 'External Sources' }, { type: 'profound', label: 'Profound Signals' },
  { type: 'inference', label: 'System inferences' }
]
const grouped = computed(() => SECTIONS.map((s) => {
  const items = props.evidence.filter((e) => e.type === s.type)
  const sources = new Set(items.map((e) => e.source).filter(Boolean))
  const label = s.type === 'profound' && !(sources.size === 1 && sources.has('profound'))
    ? 'Measured signals'
    : s.label
  return { ...s, label, items }
}).filter((s) => s.items.length))
const STATUS_LABEL: Record<string, string> = { live: 'Live', changed: 'Changed', stale: 'Stale', unavailable: 'Unavailable', failed: 'Fetch failed' }
const statusTone = (s: string) => (s === 'live' ? 'good' : s === 'changed' || s === 'stale' ? 'warn' : s === 'unavailable' ? 'muted' : 'bad')
function domain(u?: string | null) { try { return u ? new URL(u).hostname : null } catch { return null } }
</script>

<template>
  <div v-if="grouped.length" class="stack" data-testid="evidence-list">
    <section v-for="s in grouped" :key="s.type" :aria-label="s.label">
      <h3>{{ s.label }} <span class="faint">{{ s.items.length }}</span></h3>
      <ul class="stack sm" style="list-style: none; padding: 0; margin: 8px 0 0">
        <li v-for="e in s.items" :key="e.id" class="card">
          <details @toggle="($event.target as HTMLDetailsElement).open && ev.load(e.id)">
            <summary class="row spread wrap">
              <strong>{{ e.title }}</strong>
              <span class="row wrap">
                <StatusBadge :label="STATUS_LABEL[e.status] ?? e.status" :tone="statusTone(e.status)" />
                <ConfidenceBadge :value="e.confidence" />
              </span>
            </summary>
            <p class="meta">
              {{ e.domain ?? domain(e.url) ?? e.source ?? 'source not reported' }} · {{ humanize(e.type) }} ·
              <DataFreshness :at="e.observedAt ?? e.retrievedAt" prefix="Observed " /> ·
              support {{ e.supportScore?.toFixed(2) ?? '—' }} / contradiction {{ e.contradictionScore?.toFixed(2) ?? '—' }} / freshness risk {{ e.freshnessRisk?.toFixed(2) ?? '—' }}
            </p>
            <p v-if="e.status === 'failed' || e.status === 'unavailable'" class="tone-bad" role="alert">
              {{ e.status === 'failed' ? 'Could not retrieve this source.' : 'Source unavailable.' }} {{ e.error ?? '' }} Content was not inferred.
            </p>
            <p v-if="ev.loading.value[e.id]" class="meta">Fetching source extract…</p>
            <ErrorState v-else-if="ev.errors.value[e.id]" :error="ev.errors.value[e.id]!" surface="Evidence detail" @retry="ev.load(e.id)" />
            <blockquote v-else-if="ex(e)" class="extract" style="margin: 6px 0"><span class="meta">Source extract · </span>{{ ex(e) }}</blockquote>
            <p v-else-if="ev.detail.value[e.id]" class="meta">No source extract was stored for this evidence.</p>
            <p class="meta">
              <a v-if="e.url" :href="e.url" target="_blank" rel="noopener noreferrer">{{ e.url }}</a>
              <span v-if="e.contentHash"> · hash <code>{{ e.contentHash.slice(0, 12) }}</code></span>
              <span v-if="e.retrievalMethod"> · {{ e.retrievalMethod }}</span> · ID <code>{{ e.id }}</code>
            </p>
          </details>
        </li>
      </ul>
    </section>
  </div>
  <EmptyState v-else title="No evidence was retrieved for this incident." />
</template>
