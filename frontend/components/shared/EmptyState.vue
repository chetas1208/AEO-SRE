<script setup lang="ts">
// "No data" state (UI.md §47). Distinct from ErrorState (unavailable / failed).
defineProps<{ title: string; lines?: string[] }>()
</script>

<template>
  <div class="card empty" data-testid="empty-state">
    <p><strong>{{ title }}</strong></p>
    <p v-for="l in lines ?? []" :key="l" class="dim">{{ l }}</p>
    <slot />
  </div>
</template>
