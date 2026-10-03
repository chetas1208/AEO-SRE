<script setup lang="ts">
import type { ChangeCheck } from '~/types'
import DecisionChip from './DecisionChip.vue'

// Guard verdict for a proposed intervention, shown before approval. The server verdict is rendered as-is:
// BLOCK/DELAY explain why Approve is disabled, REQUIRE_REVIEW asks for a typed reason, MERGE shows the suggested merged
// proposal, ALLOW lists which checks ran and which were skipped/degraded (never implies a pass for a check that did not run).
const props = defineProps<{ guard: ChangeCheck | null | undefined; reviewReason?: string; askLater?: boolean }>()
defineEmits<{ 'update:reviewReason': [v: string] }>()
const gate = computed(() => approveGate(props.guard, props.reviewReason ?? ''))
const d = computed(() => props.guard?.decision ?? null)
const semanticDidNotRun = computed(() => !!props.guard && !semanticRan(props.guard.semanticCheck))
</script>

<template>
  <section class="stack sm guard-verdict" aria-label="Change guard verdict" data-testid="guard-verdict" :data-decision="d ?? 'unavailable'">
    <div class="row wrap" style="gap: 8px">
      <strong>Change guard</strong>
      <DecisionChip :decision="d" />
    </div>

    <p v-if="!guard" class="dim" data-testid="guard-unavailable">Guard verdict unavailable. No conflict check result was reported for this proposal; the server still enforces the guard when you approve.</p>

    <template v-else>
      <div v-if="d === 'BLOCK' || d === 'DELAY'" class="stack sm" role="alert">
        <p class="tone-bad" v-if="d === 'BLOCK'"><strong>Approval is disabled.</strong> This proposal is blocked.</p>
        <p class="tone-warn" v-else><strong>Approval is disabled.</strong> This proposal is delayed.</p>
        <p data-testid="guard-explanation">{{ gate.message }}</p>
        <p v-if="d === 'DELAY'" class="tone-warn" data-testid="eligible-after">Eligible after <strong>{{ gate.eligibleAfter ? absoluteTime(gate.eligibleAfter) : 'unavailable' }}</strong></p>
      </div>

      <div v-else-if="d === 'REQUIRE_REVIEW'" class="stack sm">
        <p class="tone-warn" role="alert"><strong>Review required.</strong> Another change on this target differs in intent or claims.</p>
        <p v-if="gate.message && guard.findings?.some((f) => f.reason)">{{ guard.findings.find((f) => f.reason)?.reason }}</p>
        <p v-if="askLater" class="meta">You will be asked for a review reason when you approve.</p>
        <label v-else class="stack sm"><span class="meta">Review reason (required; sent with the approval)</span>
          <textarea :value="reviewReason" rows="2" data-testid="review-reason" @input="$emit('update:reviewReason', ($event.target as HTMLTextAreaElement).value)" />
        </label>
      </div>

      <div v-else-if="d === 'MERGE'" class="stack sm">
        <p class="tone-info">Another pending change on this target overlaps this one. The guard suggests a merged proposal.</p>
        <pre v-if="guard.mergedProposal" data-testid="merged-proposal">{{ guard.mergedProposal }}</pre>
        <p v-else class="dim">The merged proposal was not provided.</p>
      </div>

      <div v-else-if="d === 'ALLOW'" class="stack sm">
        <p class="dim" data-testid="guard-allow">No conflicts found.</p>
      </div>

      <p v-else class="dim">Guard decision unavailable.</p>

      <ul v-if="guard.findings?.length && d !== 'ALLOW'" class="meta" style="margin: 0; padding-left: 18px">
        <li v-for="(f, i) in guard.findings" :key="i">{{ humanize(f.type) }}: {{ f.reason ?? 'reason unavailable' }}<template v-if="f.experimentCode"> ({{ f.experimentCode }})</template></li>
      </ul>

      <div class="meta stack xs" data-testid="guard-checks">
        <p v-if="guard.checksRun?.length">Checks run: {{ guard.checksRun.map(humanize).join(', ') }}</p>
        <p v-else-if="guard.checksRun === null">Checks run: not reported</p>
        <p v-if="guard.checksSkipped?.length" class="tone-warn">Skipped: {{ guard.checksSkipped.map(humanize).join(', ') }}</p>
        <p :class="{ 'tone-warn': semanticDidNotRun }" data-testid="semantic-state">{{ semanticCheckLabel(guard.semanticCheck) }}</p>
      </div>
    </template>
  </section>
</template>
