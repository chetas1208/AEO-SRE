<script setup lang="ts">
import type { ExperimentSpecInfo } from '~/types'

const props = defineProps<{
  spec?: ExperimentSpecInfo | null
  metrics?: { primary?: string | null; secondary: string[] } | null
}>()

const windowText = computed(() => {
  const w = props.spec?.windowHours, d = props.spec?.delayHours
  if (w == null && d == null) return null
  const parts: string[] = []
  if (d != null) parts.push(`${trimNum(d)}h after execution`)
  if (w != null) parts.push(`measured within a ${trimNum(w)}h verification window`)
  return parts.join(', ')
})
function trimNum(n: number) { return Number.isInteger(n) ? String(n) : n.toFixed(1) }
const direction = computed(() => props.spec?.direction ? `should ${props.spec.direction}` : null)
</script>

<template>
  <section class="card stack sm" aria-label="Experiment hypothesis" data-testid="experiment-hypothesis">
    <h3>Hypothesis (declared before execution)</h3>
    <template v-if="spec">
      <dl class="kv">
        <dt>IF</dt><dd>{{ spec.ifAction ? actionLabel(spec.ifAction) : 'Unavailable' }}</dd>
        <dt>BECAUSE</dt><dd>{{ spec.becauseRootCause ?? 'Unavailable' }}</dd>
        <dt>THEN</dt><dd>{{ spec.thenMetric ? humanize(spec.thenMetric) : 'Unavailable' }}<span v-if="direction">{{ ' ' + direction }}</span></dd>
        <dt>AFTER</dt><dd>{{ windowText ?? 'Unavailable' }}</dd>
        <dt>Primary metric</dt><dd>{{ metrics?.primary ? humanize(metrics.primary) : 'Unavailable' }}</dd>
        <dt>Secondary metrics</dt><dd>{{ metrics?.secondary?.length ? metrics.secondary.map(humanize).join(', ') : 'None declared' }}</dd>
      </dl>
      <p class="meta faint">
        A prediction to be checked, not evidence that the action works.
        <span v-if="spec.declaredAt">Declared <span :title="absoluteTime(spec.declaredAt)">{{ shortDate(spec.declaredAt) }}</span>.</span>
      </p>
    </template>
    <p v-else class="dim" data-testid="hypothesis-unavailable">Unavailable: no hypothesis was recorded for this experiment.</p>
  </section>
</template>
