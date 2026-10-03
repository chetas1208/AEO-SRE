<script setup lang="ts">
import ExperimentCreateDrawer from '~/components/experiments/ExperimentCreateDrawer.vue'

const { data, refresh } = useExperiments()
const route = useRoute()
const onDetail = computed(() => !!route.params.id)

const createModalOpen = ref(false)

function onExperimentCreated() {
  refresh()
}
</script>

<template>
  <div class="page-container floating-safe">
    <div class="stack" style="gap: 20px;">
      <!-- Action Bar -->
      <div class="experiments-action-bar">
        <div class="title-group">
          <h1 class="page-heading">Experiments Engine</h1>
          <p class="page-subheading">Change Guard protected intervention testing & causal verification ledger</p>
        </div>
        <button
          type="button"
          class="btn-new-experiment"
          data-testid="btn-new-experiment"
          @click="createModalOpen = true"
        >
          <span class="btn-icon">+</span>
          <span>New Experiment</span>
        </button>
      </div>

      <ExperimentSummary v-if="data" :list="data" />

      <div :class="['workspace', { 'on-detail': onDetail }]">
        <ExperimentQueue />
        <section class="detail-pane" aria-label="Selected experiment detail">
          <NuxtPage />
        </section>
      </div>
    </div>

    <!-- Real Experiment Creation Drawer -->
    <ExperimentCreateDrawer
      v-model="createModalOpen"
      @created="onExperimentCreated"
    />
  </div>
</template>

<style scoped>
.experiments-action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0 4px;
}

.page-heading {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #f8fafc;
}

.page-subheading {
  margin: 4px 0 0;
  font-size: 13px;
  color: #94a3b8;
}

.btn-new-experiment {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
  border: 1px solid #38bdf8;
  color: #ffffff;
  padding: 9px 18px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(2, 132, 199, 0.35);
  transition: all 0.15s ease;
}

.btn-new-experiment:hover {
  background: linear-gradient(135deg, #0369a1 0%, #075985 100%);
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.5);
  transform: translateY(-1px);
}

.btn-icon {
  font-size: 16px;
  font-weight: 700;
}
</style>
