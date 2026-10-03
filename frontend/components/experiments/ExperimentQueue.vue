<script setup lang="ts">
import type { ExperimentSummaryRow } from '~/types'

const route = useRoute()
const { data, pending, error, refresh } = useExperiments()

const currentId = computed(() => {
  return typeof route.params.id === 'string' ? route.params.id : null
})

const sortOrder = ref<'newest' | 'oldest'>('newest')

const sortedItems = computed<ExperimentSummaryRow[]>(() => {
  const items = [...(data.value?.items ?? [])]
  if (sortOrder.value === 'newest') {
    return items.sort((a, b) => new Date(b.startedAt ?? '').getTime() - new Date(a.startedAt ?? '').getTime())
  }
  return items.sort((a, b) => new Date(a.startedAt ?? '').getTime() - new Date(b.startedAt ?? '').getTime())
})
</script>

<template>
  <aside class="exp-queue stack sm" aria-label="Recent experiments queue">
    <div class="row spread">
      <h2>Recent Experiments</h2>
      <select v-model="sortOrder" class="sort-select">
        <option value="newest">Newest first</option>
        <option value="oldest">Oldest first</option>
      </select>
    </div>

    <LoadingState v-if="pending && !data" message="Loading experiments…" />
    <ErrorState v-else-if="error && !data" :error="error" surface="Experiments" @retry="refresh()" />
    <template v-else-if="data">
      <EmptyState
        v-if="!sortedItems.length"
        title="No experiments recorded yet."
        :lines="['An experiment is opened when the policy proposes an intervention.']"
      />
      <div v-else class="exp-list">
        <ExperimentCard
          v-for="item in sortedItems"
          :key="item.id"
          :experiment="item"
          :selected="currentId === item.id"
        />
      </div>
    </template>
    <LoadingState v-else message="Loading experiments…" />
  </aside>
</template>

<style scoped>
.exp-queue {
  position: sticky;
  top: 72px;
  max-height: calc(100vh - 90px);
  overflow-y: auto;
  padding-right: 4px;
}
.exp-queue::-webkit-scrollbar { width: 5px; }
.exp-queue::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

.sort-select {
  padding: 4px 8px;
  font-size: 11px;
  background: var(--bg-1);
}
.exp-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 8px;
}
</style>
