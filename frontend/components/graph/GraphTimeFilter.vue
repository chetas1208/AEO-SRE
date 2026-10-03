<script setup lang="ts">
import type { GraphWindow } from '~/types/graph'
import { GRAPH_WINDOWS } from '~/utils/graph'

defineProps<{ modelValue: GraphWindow }>()
defineEmits<{ 'update:modelValue': [v: GraphWindow] }>()
</script>

<template>
  <div class="row" role="group" aria-label="Time window" data-testid="graph-time-filter" style="gap: 4px">
    <button
      v-for="w in GRAPH_WINDOWS" :key="w.id" type="button" :aria-pressed="modelValue === w.id" :class="{ primary: modelValue === w.id }"
      :data-window="w.id" @click="$emit('update:modelValue', w.id)"
    >{{ w.label }}</button>
  </div>
</template>
