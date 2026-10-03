<script setup lang="ts">
import type { ChangeCheck } from '~/types'
import DecisionChip from './DecisionChip.vue'

// One change check: decision, agent, target, every finding with its reason, eligibility and overlap. Server data only.
const props = defineProps<{ check: ChangeCheck; compact?: boolean }>()
const open = ref(!props.compact || ['BLOCK', 'DELAY', 'REQUIRE_REVIEW'].includes(props.check.decision ?? ''))
const eligible = computed(() => props.check.eligibleAfter ?? props.check.findings?.find((f) => f.eligibleAfter)?.eligibleAfter ?? null)
</script>

<template>
  <article class="card stack sm check-card" data-testid="change-check" :data-decision="check.decision ?? 'unavailable'">
    <div class="row spread wrap">
      <div class="row wrap" style="gap: 8px">
        <DecisionChip :decision="check.decision" />
        <span v-if="check.sourceMode === 'SIMULATED'" class="badge tone-policy" data-testid="simulated-badge">SIMULATED AGENT</span>
        <span v-else-if="check.sourceMode === null" class="badge tone-muted">Source unavailable</span>
        <strong>{{ check.agentName ?? 'Agent unavailable' }}</strong>
      </div>
      <span class="meta" :title="absoluteTime(check.evaluatedAt)">{{ relativeTime(check.evaluatedAt) }}</span>
    </div>
    <p class="meta mono" style="word-break: break-all">{{ check.targetUrl ?? 'Target unavailable' }}<template v-if="check.actionType"> · {{ actionLabel(check.actionType) }}</template></p>
    <p v-if="check.decision === 'DELAY' || eligible" class="tone-warn" data-testid="eligible-after">
      Eligible after <strong>{{ eligible ? absoluteTime(eligible) : 'unavailable' }}</strong><span v-if="eligible" class="meta"> ({{ eligible }})</span>
    </p>
    <button v-if="compact" type="button" class="link" :aria-expanded="open" @click="open = !open">{{ open ? 'Hide findings' : `Show findings (${check.findings?.length ?? 'unavailable'})` }}</button>
    <template v-if="open">
      <p v-if="check.findings === null" class="dim">Findings unavailable.</p>
      <p v-else-if="!check.findings.length" class="dim">No findings reported.</p>
      <ul v-else class="stack sm findings" style="margin: 0; padding-left: 18px">
        <li v-for="(f, i) in check.findings" :key="i" data-testid="check-finding">
          <span class="row wrap" style="gap: 6px">
            <DecisionChip v-if="f.decision" :decision="f.decision" />
            <span class="meta">{{ humanize(f.type) || 'Finding' }}<template v-if="f.severity"> · {{ humanize(f.severity) }}</template><template v-if="f.outcomeClass"> · {{ humanize(f.outcomeClass) }}</template></span>
          </span>
          <p>{{ f.reason ?? 'Reason unavailable.' }}</p>
          <p v-if="f.experimentCode || f.otherChangeId || f.canonicalClaimId" class="meta">
            <template v-if="f.experimentCode">Experiment <NuxtLink :to="`/experiments/${f.experimentCode}`">{{ f.experimentCode }}</NuxtLink> </template>
            <template v-if="f.otherChangeId">Other change {{ f.otherChangeId }} </template>
            <template v-if="f.canonicalClaimId">Canonical claim {{ f.canonicalClaimId }}</template>
          </p>
          <p v-if="f.targetOverlapPct !== null || f.promptClusterOverlapPct !== null" class="meta" data-testid="overlap">
            Target overlap {{ pct(f.targetOverlapPct) }} · Prompt-cluster overlap {{ pct(f.promptClusterOverlapPct) }}
          </p>
          <p v-if="f.eligibleAfter" class="meta">Eligible after {{ absoluteTime(f.eligibleAfter) }}</p>
        </li>
      </ul>
      <p class="meta">{{ semanticCheckLabel(check.semanticCheck) }}</p>
    </template>
  </article>
</template>
