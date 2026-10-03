<script setup lang="ts">
const { data } = useExperiments()
const route = useRoute()
const onDetail = computed(() => !!route.params.id)
</script>

<template>
  <div class="page-container floating-safe">
    <div class="stack" style="gap: 20px;">
      <ExperimentSummary v-if="data" :list="data" />
      <div :class="['workspace', { 'on-detail': onDetail }]">
        <ExperimentQueue />
        <section class="detail-pane" aria-label="Selected experiment detail">
          <NuxtPage />
        </section>
      </div>
    </div>
  </div>
</template>
