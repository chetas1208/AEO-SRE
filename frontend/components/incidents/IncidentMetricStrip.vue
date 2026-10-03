<script setup lang="ts">
import type { MetricDelta } from '~/types'

// Backend picks the relevant metrics (max four); this only renders the generic schema.
const props = defineProps<{ metrics: MetricDelta[] }>()
const shown = computed(() => props.metrics.slice(0, 4))
</script>

<template>
  <div v-if="shown.length" class="metric-strip" data-testid="metric-strip">
    <MetricCard v-for="(m, k) in shown" :key="m.key ?? m.label + k" :metric="m" />
  </div>
  <EmptyState v-else title="No metrics were reported for this incident." />
</template>
