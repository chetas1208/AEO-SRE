<script setup lang="ts">
const route = useRoute()
const onDetail = computed(() => !!route.params.id)
</script>

<template>
  <div :class="['workspace', { 'on-detail': onDetail }]">
    <IncidentQueue />
    <section class="detail-pane" aria-label="Selected incident">
      <NuxtPage />
    </section>
  </div>
</template>
