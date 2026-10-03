<script setup lang="ts">
import { reasonText } from '~/utils/graph'

// Honest unavailable/stale state for the graph. Never shows nodes that the API did not return.
defineProps<{ kind: 'unavailable' | 'stale'; reason?: string | null; lastGoodAt?: string | null; surface?: string }>()
</script>

<template>
  <div class="card stack sm graph-unavailable" :data-kind="kind" data-testid="graph-unavailable" role="status">
    <strong :class="kind === 'stale' ? 'tone-warn' : 'tone-bad'">{{ kind === 'stale' ? 'Graph stale' : 'Graph unavailable' }}<template v-if="surface"> ({{ surface }})</template></strong>
    <p v-if="kind === 'unavailable'" class="dim">The relationship graph cannot be read right now. Approvals, experiments and Change Guard checks are unaffected (they run on the system of record).</p>
    <p v-else class="dim">The graph projection is behind the system of record. Anything shown may be out of date; the baseline policy is used until it catches up.</p>
    <p class="meta" data-testid="graph-unavailable-reason">Reason: {{ reasonText(reason) }}</p>
    <p class="meta" data-testid="graph-last-good">Last good graph: {{ lastGoodAt ? `${absoluteTime(lastGoodAt)} (${lastGoodAt})` : 'none recorded in this session' }}</p>
  </div>
</template>
