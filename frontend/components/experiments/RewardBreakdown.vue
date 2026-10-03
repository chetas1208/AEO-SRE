<script setup lang="ts">
import type { RewardData } from '~/types'

const props = defineProps<{ reward?: RewardData | null; status?: string; dryRun?: boolean | null }>()
const comps = computed(() => Object.entries(props.reward?.components ?? {}))
</script>

<template>
  <section class="card stack sm" aria-label="Reward decomposition" data-testid="reward-breakdown">
    <h3>Reward</h3>
    <template v-if="reward && reward.total != null">
      <p><strong>Total reward: {{ signed(reward.total) }}</strong> <span class="meta">computed by backend <DataFreshness :at="reward.computedAt" /></span></p>
      <details open>
        <summary>Decomposition</summary>
        <table>
          <thead><tr><th scope="col">Component</th><th scope="col">Value</th><th v-if="reward.weights" scope="col">Weight</th></tr></thead>
          <tbody>
            <tr v-for="[k, v] in comps" :key="k">
              <th scope="row">{{ humanize(k) }}</th><td>{{ signed(v) }}</td><td v-if="reward.weights">{{ reward.weights[k] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </template>
    <p v-else-if="dryRun" class="dim">Never measured: this was a GitHub dry-run preview (nothing was changed), so no reward exists or will be computed.</p>
    <p v-else class="dim">{{ status === 'awaiting_verification' ? 'Reward pending: awaiting measured post-intervention observation.' : 'No reward has been computed for this experiment.' }}</p>
  </section>
</template>
