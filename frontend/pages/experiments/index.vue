<script setup lang="ts">
const { data, pending } = useExperiments()

watchEffect(() => {
  const items = data.value?.items
  if (items && items.length > 0) {
    const first = items[0]
    if (first) {
      navigateTo(`/experiments/${first.id}`, { replace: true })
    }
  }
})
</script>

<template>
  <div class="card stack sm">
    <LoadingState v-if="pending" message="Loading experiment ledger…" />
    <EmptyState
      v-else-if="!data?.items.length"
      title="No experiments recorded."
      :lines="['Experiments are logged when an intervention is proposed and executed.']"
    />
    <ChangeChecksPanel v-if="!pending && !data?.items.length" />
    <p v-else class="dim">Selecting primary experiment…</p>
  </div>
</template>
