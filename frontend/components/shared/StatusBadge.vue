<script setup lang="ts">
// Text label + tone; never colour-only.
defineProps<{ label: string; tone?: 'info' | 'warn' | 'good' | 'bad' | 'muted' | 'policy' }>()
</script>

<template>
  <span :class="['badge', `tone-${tone ?? 'muted'}`]" data-testid="status-badge">{{ label }}</span>
</template>
