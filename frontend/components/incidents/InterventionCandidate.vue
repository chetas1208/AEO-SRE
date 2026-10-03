<script setup lang="ts">
import type { InterventionCandidate } from '~/types'

// "Candidate intervention" (not "AI suggestion"). Score is a policy score, not a probability.
const props = defineProps<{ candidate: InterventionCandidate; disabled?: boolean }>()
defineEmits<{ choose: [c: InterventionCandidate] }>()
const c = computed(() => props.candidate)
</script>

<template>
  <div :class="['card', { selected: c.selected }]" data-testid="intervention-candidate">
    <div class="row spread wrap">
      <strong>{{ c.title || actionLabel(c.action) }}</strong>
      <span class="row wrap">
        <StatusBadge v-if="c.selected" label="Recommended" tone="info" />
        <StatusBadge :label="`Risk: ${humanize(c.risk)}`" :tone="c.risk === 'high' ? 'bad' : c.risk === 'medium' ? 'warn' : 'good'" />
        <span class="mono" :title="'Policy score (not a probability)'">{{ c.score.toFixed(2) }}</span>
      </span>
    </div>
    <p v-if="c.reason" class="dim">{{ c.reason }}</p>
    <div class="row spread wrap">
      <span class="meta">{{ actionLabel(c.action) }}<template v-if="c.policyProbability != null"> · selection probability {{ c.policyProbability.toFixed(2) }}</template></span>
      <button v-if="!c.selected" type="button" :disabled="disabled" @click="$emit('choose', c)">
        {{ c.action === 'observe' ? 'Choose Observe' : 'Choose this action' }}
      </button>
    </div>
  </div>
</template>
