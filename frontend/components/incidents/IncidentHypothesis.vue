<script setup lang="ts">
import type { EvidenceItem, Hypothesis, ApiErrorInfo } from '~/types'

const props = defineProps<{ hypothesis: Hypothesis; rank: number; incidentId: string; evidence: EvidenceItem[] | null; evidenceError?: ApiErrorInfo | null }>()
const open = ref(false)
const ev = useEvidenceDetail(() => props.incidentId)
const h = computed(() => props.hypothesis)
const linked = computed(() => (props.evidence ?? []).filter((e) => h.value.evidenceIds.includes(e.id)).map((e) => ({ ...e, excerpt: ev.detail.value[e.id]?.excerpt ?? e.excerpt })))
watch(open, (o) => { if (o) h.value.evidenceIds.forEach((id) => ev.load(id)) })
function domain(u?: string | null) { try { return u ? new URL(u).hostname : null } catch { return null } }
</script>

<template>
  <div class="card" data-testid="hypothesis">
    <button class="link row spread" type="button" style="width: 100%; text-align: left" :aria-expanded="open" @click="open = !open">
      <span><strong>{{ rank }}. {{ h.title }}</strong></span>
      <span class="row">
        <StatusBadge v-if="h.status === 'confirmed'" label="Confirmed" tone="good" />
        <StatusBadge v-else-if="h.status === 'rejected'" label="Rejected" tone="muted" />
        <StatusBadge v-else label="Proposed" tone="policy" />
        <ConfidenceBadge :value="h.confidence" />
      </span>
    </button>
    <p v-if="h.summary" class="interp">{{ h.summary }}</p>
    <div v-if="open" class="stack sm" style="margin-top: 8px">
      <p v-if="h.rationale" class="interp"><span class="meta">System interpretation · </span>{{ h.rationale }}</p>
      <p class="meta">Produced by: {{ h.producedBy ?? 'not reported' }} · Evidence IDs: {{ h.evidenceIds.length ? h.evidenceIds.join(', ') : 'none' }}<template v-if="h.contradictingEvidenceIds?.length"> · Contradicting evidence IDs: {{ h.contradictingEvidenceIds.join(', ') }}</template></p>
      <ErrorState v-if="evidenceError" :error="evidenceError" surface="Evidence" />
      <EmptyState v-else-if="!h.evidenceIds.length || (evidence && !linked.length)" title="No supporting evidence was retrieved for this hypothesis." />
      <ul v-else class="stack sm" style="list-style: none; padding: 0; margin: 0">
        <li v-for="e in linked" :key="e.id" class="card">
          <div class="row spread wrap">
            <strong>{{ e.title }}</strong>
            <StatusBadge :label="humanize(e.status)" :tone="e.status === 'live' ? 'good' : e.status === 'changed' ? 'warn' : e.status === 'stale' ? 'warn' : 'bad'" />
          </div>
          <p class="meta">
            <a v-if="e.url" :href="e.url" target="_blank" rel="noopener noreferrer">{{ e.domain ?? domain(e.url) ?? e.url }}</a>
            <span v-else>{{ e.source ?? 'source not reported' }}</span>
            · <DataFreshness :at="e.observedAt ?? e.retrievedAt" prefix="Observed " />
            · support {{ e.supportScore?.toFixed(2) ?? '—' }} / contradiction {{ e.contradictionScore?.toFixed(2) ?? '—' }}
          </p>
          <blockquote v-if="e.excerpt" class="extract" style="margin: 6px 0 0"><span class="meta">Source extract · </span>{{ e.excerpt }}</blockquote>
          <p v-else-if="e.error" class="meta tone-bad">Retrieval error: {{ e.error }}</p>
        </li>
      </ul>
    </div>
  </div>
</template>
