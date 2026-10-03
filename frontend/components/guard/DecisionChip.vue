<script setup lang="ts">
import type { GuardDecision } from '~/types'

// Decision chip: glyph + text label + tone. Never colour-only (UI.md 43). Null decision renders "Unavailable".
const props = defineProps<{ decision?: GuardDecision | null }>()
const meta = computed(() => decisionMeta(props.decision))
</script>

<template>
  <span :class="['badge', `tone-${meta.tone}`]" data-testid="decision-chip" :data-decision="decision ?? 'unavailable'" :title="meta.summary">
    <span aria-hidden="true">{{ meta.glyph }}</span> {{ meta.label }}
  </span>
</template>
