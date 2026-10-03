<script setup lang="ts">
import type { ApiErrorInfo } from '~/types'

// Exact error copy (UI.md §48). Distinguishes unavailable data from failed data; "no data" is EmptyState.
const props = defineProps<{ error: ApiErrorInfo; surface: string; note?: string }>()
defineEmits<{ retry: [] }>()
const heading = computed(() => (props.error.kind === 'unavailable' ? `${props.surface} unavailable.` : `${props.surface} request failed.`))
</script>

<template>
  <div class="card" role="alert" data-testid="error-state">
    <p>
      <span :class="['badge', props.error.kind === 'unavailable' ? 'tone-muted' : 'tone-bad']">
        {{ props.error.kind === 'unavailable' ? 'Unavailable data' : 'Failed data' }}
      </span>
    </p>
    <p><strong>{{ heading }}</strong></p>
    <p class="dim">{{ props.error.message }}</p>
    <p v-if="props.error.detail" class="meta">{{ props.error.detail }}</p>
    <p v-if="note" class="dim">{{ note }}</p>
    <button type="button" @click="$emit('retry')">Retry</button>
  </div>
</template>
