<script setup lang="ts">
import type { GuardDecision } from '~/types'

// "Change checks" panel/feed. scope=experiment lists checks against one experiment (experiment_code); scope=all is the
// organization-wide recent feed. Live: refetches on change_check.* SSE events (useChangeChecks).
const props = defineProps<{ experimentCode?: string | null; embedded?: boolean }>()
const scope = ref<'experiment' | 'all'>(props.experimentCode ? 'experiment' : 'all')
const filter = ref<GuardDecision | ''>('')
const DECISIONS: GuardDecision[] = ['BLOCK', 'DELAY', 'REQUIRE_REVIEW', 'MERGE', 'ALLOW']
const checks = useChangeChecks({ experimentCode: () => (scope.value === 'experiment' ? props.experimentCode : null), decision: filter })
const live = useLiveSystemStore()
const list = computed(() => checks.data.value)
</script>

<template>
  <section class="stack sm" aria-label="Change checks" data-testid="change-checks-panel">
    <div class="row spread wrap">
      <h3>Change checks</h3>
      <div class="row wrap" style="gap: 8px">
        <span class="meta" :data-state="live.globalStream" data-testid="checks-live">{{ live.globalStream === 'connected' ? 'Live: updates as checks arrive' : 'Live updates unavailable' }}</span>
        <div v-if="experimentCode" class="row" role="group" aria-label="Scope" style="gap: 4px">
          <button type="button" :aria-pressed="scope === 'experiment'" :class="{ primary: scope === 'experiment' }" @click="scope = 'experiment'">This experiment</button>
          <button type="button" :aria-pressed="scope === 'all'" :class="{ primary: scope === 'all' }" @click="scope = 'all'">All recent</button>
        </div>
        <label class="row" style="gap: 4px"><span class="meta">Decision</span>
          <select v-model="filter" aria-label="Filter by decision">
            <option value="">All</option>
            <option v-for="d in DECISIONS" :key="d" :value="d">{{ decisionMeta(d).label }}</option>
          </select>
        </label>
      </div>
    </div>
    <LoadingState v-if="checks.pending.value && !list" message="Loading change checks…" />
    <ErrorState v-else-if="checks.error.value && !list" :error="checks.error.value" surface="Change checks" @retry="checks.refresh()" />
    <EmptyState v-else-if="!list?.items.length" title="No change checks recorded." :lines="[filter ? 'No checks match this decision filter.' : 'Agents post intended changes to the Change Guard; each evaluation appears here.']" />
    <template v-else>
      <CheckCard v-for="c in list.items" :key="c.id" :check="c" compact />
      <div class="row spread">
        <span class="meta">Page {{ checks.page.value + 1 }}<template v-if="checks.total.value != null"> · {{ checks.total.value }} total</template></span>
        <div class="row">
          <button type="button" :disabled="checks.page.value === 0" @click="checks.prev()">Previous</button>
          <button type="button" :disabled="!checks.hasNext.value" @click="checks.next()">Next</button>
        </div>
      </div>
    </template>
  </section>
</template>
