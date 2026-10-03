<script setup lang="ts">
import type { ExperimentDetail } from '~/types'

const props = defineProps<{ experiment: ExperimentDetail }>()
const e = computed(() => props.experiment)
const o = computed(() => e.value.outcome ?? null)
const v = computed(() => e.value.verification ?? null)
const ov = computed(() => e.value.override ?? null)

const api = useVerifyExperiment(() => props.experiment.id)
const canVerify = computed(() =>
  !e.value.dryRun
  && ['executed', 'awaiting_verification'].includes(e.value.status)
  && v.value?.isOpen === true)
const verifyHint = computed(() => {
  if (e.value.dryRun || !['executed', 'awaiting_verification'].includes(e.value.status)) return null
  if (v.value?.isOpen === true) return null
  if (v.value?.eligibleAt) return `Verification unavailable until ${absoluteTime(v.value.eligibleAt)}.`
  return 'Verification is not open yet; the backend sets the measurement window after execution.'
})

const LABELS: Record<string, string> = { favorable: 'Favorable', unfavorable: 'Unfavorable', neutral: 'Neutral', inconclusive: 'Inconclusive' }
const TONES: Record<string, string> = { favorable: 'tone-good', unfavorable: 'tone-bad', neutral: 'tone-muted', inconclusive: 'tone-warn' }
</script>

<template>
  <section class="card stack sm" aria-label="Outcome and verification" data-testid="experiment-verdict">
    <h3>Outcome</h3>
    <template v-if="o">
      <p>
        <strong :class="TONES[o.label] ?? 'tone-muted'" data-testid="outcome-label">{{ LABELS[o.label] ?? humanize(o.label) }}</strong>
        <span v-if="o.observeOutcome" class="meta dim"> ({{ humanize(o.observeOutcome) }})</span>
      </p>
      <p v-if="o.label === 'inconclusive'" class="tone-warn meta" data-testid="inconclusive-reason">
        No conclusion could be drawn: {{ o.inconclusiveReason ?? 'reason not recorded' }}. No policy update was made; this is not evidence for or against the action.
      </p>
      <dl class="kv">
        <dt>Causal confidence</dt><dd>{{ o.causalConfidence ? humanize(o.causalConfidence) : 'Unavailable' }}</dd>
        <dt>Policy updated</dt><dd>{{ o.learningApplied ? 'Yes' : 'No' }}</dd>
        <dt>Measured</dt><dd>{{ o.observedAt ? absoluteTime(o.observedAt) : 'Unavailable' }}</dd>
      </dl>
      <p v-if="o.causalStatement" class="meta faint">{{ o.causalStatement }}</p>
    </template>
    <p v-else class="dim" data-testid="outcome-pending">Not assessed yet. The outcome is determined by the backend after a qualifying post-intervention measurement.</p>

    <template v-if="v">
      <h4>Verification window</h4>
      <dl class="kv">
        <dt>Eligible at</dt>
        <dd data-testid="eligible-at">{{ v.eligibleAt ? absoluteTime(v.eligibleAt) : 'Unavailable' }}<span v-if="v.isOpen != null" class="meta dim"> ({{ v.isOpen ? 'open' : 'not open yet' }})</span></dd>
        <dt>Window ends</dt><dd>{{ v.windowEnd ? absoluteTime(v.windowEnd) : 'Unavailable' }}</dd>
        <dt>Executed</dt><dd>{{ v.executedAt ? absoluteTime(v.executedAt) : 'Not executed yet' }}</dd>
      </dl>
      <p v-if="verifyHint" class="meta tone-warn" data-testid="verify-blocked">{{ verifyHint }}</p>
      <div v-else-if="canVerify" class="stack xs">
        <button type="button" :disabled="api.submitting.value" data-testid="verify-request" @click="api.verify()">
          {{ api.submitting.value ? 'Requesting…' : 'Request verification' }}
        </button>
        <p v-if="api.error.value" class="tone-warn meta" role="alert" data-testid="verify-error">{{ api.error.value.message }}</p>
        <p v-else-if="api.queued.value" class="meta dim" data-testid="verify-queued">Verification requested. The outcome appears only after the backend measures it.</p>
      </div>
      <details v-if="v.rules.length">
        <summary class="meta">Measurement rules</summary>
        <ul class="meta"><li v-for="r in v.rules" :key="r">{{ r }}</li></ul>
      </details>
    </template>

    <template v-if="ov?.overridden">
      <h4>Human override</h4>
      <dl class="kv" data-testid="override">
        <dt>Policy chose</dt><dd>{{ ov.policyAction ? actionLabel(ov.policyAction) : 'Unavailable' }}</dd>
        <dt>Executed</dt><dd>{{ ov.executedAction ? actionLabel(ov.executedAction) : 'Unavailable' }}</dd>
        <dt>Reason</dt><dd>{{ ov.reason ?? 'Not recorded' }}</dd>
        <dt>By</dt><dd>{{ ov.by ?? 'Not recorded' }}</dd>
      </dl>
      <p class="meta faint">The outcome is attributed to the executed action; the policy propensity is not used for learning.</p>
    </template>
  </section>
</template>
