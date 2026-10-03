<script setup lang="ts">
// Copy comes from the caller (UI.md §46): 'Loading incidents…', 'Fetching sources…', 'Computing intervention policy…'
defineProps<{ message: string }>()
</script>

<template>
  <div class="card loading" role="status" aria-live="polite" data-testid="loading-state">
    <span class="dim">{{ message }}</span>
  </div>
</template>
