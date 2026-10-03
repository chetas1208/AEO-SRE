<script setup lang="ts">
import type { Severity } from '~/types'

// Severity comes from the backend and is only rendered here.
const props = defineProps<{ severity: Severity }>()
const tone = computed(() => ({ critical: 'bad', high: 'bad', medium: 'warn', low: 'muted' } as const)[props.severity] ?? 'muted')
</script>

<template>
  <span :class="['badge', `tone-${tone}`]" :aria-label="`Severity: ${SEVERITY_LABEL[props.severity] ?? props.severity}`" data-testid="severity-badge">
    {{ SEVERITY_LABEL[props.severity] ?? props.severity }}
  </span>
</template>
