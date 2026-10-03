<script setup lang="ts">
// Number + label (UI.md §50); backend 0–1 score is shown as-is, not as a percentage.
const props = defineProps<{ value?: number | null }>()
const tone = computed(() => (props.value == null ? 'muted' : props.value >= 0.75 ? 'good' : props.value >= 0.5 ? 'warn' : 'bad'))
</script>

<template>
  <span :class="['badge', `tone-${tone}`]" data-testid="confidence-badge">
    <template v-if="props.value != null">{{ props.value.toFixed(2) }} · {{ confidenceLabel(props.value) }}</template>
    <template v-else>Confidence not reported</template>
  </span>
</template>
