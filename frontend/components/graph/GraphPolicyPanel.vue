<script setup lang="ts">
import type { GContext } from '~/types/graph'
import { fmtFeature, fmtRate, policyHeadline, policyPanelState, reasonText, shadowLabel } from '~/utils/graph'

// Small policy panel (not 3D): every value is read from the API, unknown/null renders "unavailable". No decision logic here.
const props = defineProps<{ context: GContext | null | undefined }>()
const state = computed(() => policyPanelState(props.context))
const p = computed(() => props.context?.policy ?? null)
</script>

<template>
  <section class="card stack sm" aria-label="Graph-aware control policy" data-testid="graph-policy-panel" :data-state="state">
    <template v-if="state === 'unavailable'">
      <strong>Policy unavailable</strong>
      <p class="dim" data-testid="policy-unavailable">No policy recommendation was reported for this change<template v-if="context?.reason">: {{ reasonText(context.reason) }}</template>. The deterministic Change Guard verdict still applies.</p>
    </template>
    <template v-else>
      <div class="row spread wrap">
        <strong data-testid="policy-headline">{{ policyHeadline(context) }}</strong>
        <span v-if="shadowLabel(context)" class="badge tone-policy" data-testid="policy-shadow">{{ shadowLabel(context) }}</span>
      </div>
      <p v-if="state === 'stale'" class="tone-warn" role="status" data-testid="policy-stale">Graph stale: the baseline policy was used. {{ reasonText(context?.reason) }}</p>
      <p v-else-if="state === 'not_graduated'" class="meta" data-testid="policy-not-graduated">Not graduated: the graph policy runs in shadow; the baseline policy decides.</p>
      <dl class="kv">
        <dt>Eligible actions</dt><dd data-testid="policy-eligible">{{ p?.eligibleActions?.length ? p.eligibleActions.join(', ') : p?.eligibleActions ? 'none' : 'unavailable' }}</dd>
        <dt>Selected action</dt><dd data-testid="policy-selected">{{ p?.selectedAction ?? 'unavailable' }}</dd>
        <dt v-if="p?.baselineAction">Baseline action</dt><dd v-if="p?.baselineAction">{{ p.baselineAction }}</dd>
      </dl>
      <details open>
        <summary class="meta">Key graph features<template v-if="context?.featureVersion"> ({{ context.featureVersion }})</template></summary>
        <p v-if="!context?.features.length" class="dim">Graph features unavailable.</p>
        <dl v-else class="kv" data-testid="policy-features">
          <template v-for="f in context.features" :key="f.name"><dt class="mono">{{ f.name }}</dt><dd class="mono">{{ fmtFeature(f) }}</dd></template>
        </dl>
        <p v-if="context?.snapshotTime" class="meta">Graph snapshot {{ absoluteTime(context.snapshotTime) }}</p>
      </details>
      <div data-testid="policy-neighbors">
        <p class="meta">Similar historical contexts</p>
        <p v-if="!context?.neighbors" class="dim">Historical context unavailable.</p>
        <template v-else>
          <p>{{ context.neighbors.count ?? 'unavailable' }} similar<template v-if="context.neighbors.count === 1"> context</template><template v-else> contexts</template>
            · allow {{ fmtRate(context.neighbors.allowRate) }} · block {{ fmtRate(context.neighbors.blockRate) }} · review {{ fmtRate(context.neighbors.reviewRate) }}</p>
          <ul v-if="context.neighbors.byDecision.length" class="meta" style="margin: 0; padding-left: 18px">
            <li v-for="d in context.neighbors.byDecision" :key="d.decision">{{ d.decision }}: {{ d.n ?? 'unavailable' }} (positive {{ fmtRate(d.positiveRate) }})</li>
          </ul>
        </template>
      </div>
    </template>
  </section>
</template>
