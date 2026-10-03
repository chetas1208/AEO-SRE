<script setup lang="ts">
import type { ExperimentSummaryRow } from '~/types'

defineProps<{ rows: ExperimentSummaryRow[] }>()
</script>

<template>
  <div style="overflow-x: auto">
    <table data-testid="experiment-table">
      <thead>
        <tr>
          <th scope="col">Experiment</th><th scope="col">Incident</th><th scope="col">Action</th><th scope="col">Executor</th><th scope="col">Started</th>
          <th scope="col">Status</th><th scope="col">Before</th><th scope="col">After</th><th scope="col">Reward</th><th scope="col">Policy</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="e in rows" :key="e.id">
          <td><NuxtLink :to="`/experiments/${e.id}`">{{ e.code ?? e.id.slice(0, 8) }}</NuxtLink></td>
          <td><NuxtLink v-if="e.incidentId" :to="`/incidents/${e.incidentId}`">{{ e.incidentTitle ?? e.incidentId }}</NuxtLink><span v-else>—</span></td>
          <td>{{ e.actionTitle ?? actionLabel(e.action) }}</td>
          <td>{{ executorLabel(e.executor) }}</td>
          <td :title="absoluteTime(e.startedAt)">{{ shortDate(e.startedAt) }}</td>
          <td><StatusBadge :label="e.displayStatus ?? experimentLabel(e.status)" :tone="experimentTone(e.status)" /></td>
          <td :title="e.metricLabel ?? undefined">{{ e.before != null ? formatValue(e.before, e.unit) : '—' }}</td>
          <td :title="e.metricLabel ?? undefined">{{ e.after != null ? formatValue(e.after, e.unit) : '—' }}</td>
          <td>{{ e.reward != null ? signed(e.reward) : '—' }}</td>
          <td>{{ e.policyVersion ?? '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
