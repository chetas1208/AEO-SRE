<script setup lang="ts">
import type { ExperimentDetail, MetricDelta } from '~/types'

const props = defineProps<{
  experiment: ExperimentDetail
}>()

const emit = defineEmits<{
  viewChanges: []
}>()

const e = computed(() => props.experiment)

const isAwaiting = computed(() => {
  return e.value.status === 'awaiting_verification' || (e.value.awaitingReward && !['verified', 'rewarded', 'rejected', 'failed'].includes(e.value.status))
})

const isExecuted = computed(() => {
  return ['executed', 'awaiting_verification', 'verified', 'rewarded'].includes(e.value.status)
})

const isApproved = computed(() => {
  return (
    !!e.value.approval?.status && ['approved', 'modified'].includes(String(e.value.approval.status).toLowerCase())
  ) || ['approved', 'executing', 'executed', 'awaiting_verification', 'verified', 'rewarded'].includes(e.value.status)
})

const delayedChecks = computed(() => {
  const rows = e.value.protection?.recentChecks ?? []
  return rows.filter(c => c.decision === 'DELAY' || c.decision === 'BLOCK')
})

const protectionUntil = computed(() => e.value.protection?.until ?? eligibleAfter.value)

const isVerified = computed(() => {
  return ['verified', 'rewarded'].includes(e.value.status)
})

const isRewarded = computed(() => {
  return e.value.status === 'rewarded' || (e.value.rewardData != null && !e.value.awaitingReward)
})

// Current active step index (1 to 9)
const activeStep = computed(() => {
  if (isRewarded.value) return 9
  if (isVerified.value) return 8
  if (isAwaiting.value) return 5 // Currently waiting for verification window
  if (isExecuted.value) return 5
  if (isApproved.value) return 4
  return 2
})

function stepState(stepNum: number): 'completed' | 'current' | 'future' {
  if (stepNum < activeStep.value) return 'completed'
  if (stepNum === activeStep.value) return 'current'
  return 'future'
}

const eligibleAfter = computed(() => {
  return e.value.verification?.eligibleAt ?? e.value.verificationWindowStart ?? null
})

const targetSurface = computed(() => {
  const pc = e.value.proposedChange as { target_url?: string; targetUrl?: string } | null | undefined
  return e.value.protection?.targets?.[0] ?? pc?.target_url ?? pc?.targetUrl ?? 'Protected surface'
})

function getMetricVal(metrics: MetricDelta[] | Record<string, number> | null | undefined, key: string): number | null {
  if (!metrics) return null
  if (Array.isArray(metrics)) {
    const cleanKey = key.toLowerCase().replace(/\s+/g, '')
    const found = metrics.find(m => (m.key ?? m.label).toLowerCase().replace(/\s+/g, '').includes(cleanKey))
    return found?.after ?? found?.before ?? found?.value ?? null
  }
  return (metrics as Record<string, number>)[key] ?? null
}
</script>

<template>
  <div class="causal-spine" aria-label="9-step intervention workflow">
    <!-- Step 1: Intervention -->
    <div class="spine-step" :class="stepState(1)">
      <div class="step-marker" aria-hidden="true">1</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">1. Intervention</strong>
          <button v-if="e.proposedChange?.diff || e.package?.diff || e.actualChange" class="view-changes-btn" type="button" @click="$emit('viewChanges')">
            View changes
          </button>
        </div>
        <p class="step-text bold-white">
          {{ e.actionTitle ?? actionLabel(e.action) }}
        </p>
        <p v-if="e.executionNote" class="step-meta">
          {{ e.executionNote }}
        </p>
        <p v-else-if="e.rationale" class="step-meta">
          {{ e.rationale }}
        </p>
      </div>
    </div>

    <!-- Step 2: Approval -->
    <div class="spine-step" :class="stepState(2)">
      <div class="step-marker" aria-hidden="true">2</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">2. Approval</strong>
          <span v-if="isApproved" class="step-status tone-good">Approved</span>
          <span v-else class="step-status tone-warn">Pending authorization</span>
        </div>
        <p v-if="isApproved" class="step-text">
          <strong>{{ e.approval?.decidedBy ?? 'Authorized operator' }}</strong>
          <span class="dim"> · {{ absoluteTime(e.approval?.decidedAt ?? e.executedAt ?? e.startedAt) }}</span>
        </p>
        <p v-else class="step-text dim">Awaiting human approval before execution.</p>
      </div>
    </div>

    <!-- Step 3: Protected Targets -->
    <div class="spine-step" :class="stepState(3)">
      <div class="step-marker" aria-hidden="true">3</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">3. Protected Targets</strong>
          <span class="step-status tone-good">ACTIVE PROTECTION</span>
        </div>
        <div class="stack xs" style="margin-top: 4px;">
          <div class="row spread wrap">
            <span class="step-text bold-white">{{ targetSurface }}</span>
          </div>
          <p v-if="protectionUntil" class="step-meta">
            Guarded until <strong>{{ absoluteTime(protectionUntil) }}</strong>
          </p>
        </div>
      </div>
    </div>

    <!-- Step 4: Baseline (Before) -->
    <div class="spine-step" :class="stepState(4)">
      <div class="step-marker" aria-hidden="true">4</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">4. Baseline (Before)</strong>
          <span class="step-meta">Measured over 7 days before intervention</span>
        </div>
        <div class="baseline-metrics-row">
          <div class="base-metric">
            <span class="lbl">Visibility</span>
            <span class="val">{{ getMetricVal(e.beforeMetrics, 'visibility') != null ? formatValue(getMetricVal(e.beforeMetrics, 'visibility'), '%') : '—' }}</span>
          </div>
          <div class="base-metric">
            <span class="lbl">Citation Share</span>
            <span class="val">{{ getMetricVal(e.beforeMetrics, 'citation') != null ? formatValue(getMetricVal(e.beforeMetrics, 'citation'), '%') : '—' }}</span>
          </div>
          <div class="base-metric">
            <span class="lbl">Competitor Share</span>
            <span class="val">{{ getMetricVal(e.beforeMetrics, 'competitor') != null ? formatValue(getMetricVal(e.beforeMetrics, 'competitor'), '%') : '—' }}</span>
          </div>
          <div class="base-metric">
            <span class="lbl">Prompt Volume</span>
            <span class="val">{{ getMetricVal(e.beforeMetrics, 'volume') != null ? formatValue(getMetricVal(e.beforeMetrics, 'volume'), '') : '—' }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Step 5: Measurement Window -->
    <div class="spine-step" :class="stepState(5)">
      <div class="step-marker" aria-hidden="true">5</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">5. Measurement Window</strong>
          <span v-if="e.verification?.delayHours != null" class="step-meta">{{ e.verification.delayHours }}h after execution</span>
        </div>
        <div class="eligibility-box">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tone-warn" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span class="eligibility-text">
            <strong>Eligible after:</strong> {{ eligibleAfter ? absoluteTime(eligibleAfter) : 'Pending execution schedule' }}
          </span>
        </div>
      </div>
    </div>

    <!-- Step 6: Incoming Change Conflicts -->
    <div class="spine-step" :class="stepState(6)">
      <div class="step-marker" aria-hidden="true">6</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">6. Incoming Change Conflicts</strong>
          <span v-if="delayedChecks.length" class="step-status tone-warn">{{ delayedChecks.length }} change{{ delayedChecks.length === 1 ? '' : 's' }} delayed</span>
          <span v-else class="step-status tone-muted">No conflicts</span>
        </div>
        <p v-if="delayedChecks.length" class="step-text dim">
          Incoming agent changes touching this target were delayed to prevent measurement contamination:
        </p>
        <p v-else class="step-text dim">No competing changes were held during this measurement window.</p>
        <div v-if="delayedChecks.length" class="stack xs" style="margin-top: 6px;">
          <div v-for="check in delayedChecks" :key="check.id" class="row spread wrap delayed-change-item">
            <div class="row" style="gap: 8px;">
              <span class="badge tone-warn">{{ check.decision }}</span>
              <strong style="color: #ffffff;">{{ check.agentName ?? 'Agent change' }}</strong>
              <span v-if="check.actionType || check.targetUrl" class="meta dim">{{ check.actionType ?? 'UPDATE' }} {{ check.targetUrl ?? '' }}</span>
            </div>
            <NuxtLink to="/incidents" class="link meta">Open in Change Guard →</NuxtLink>
          </div>
        </div>
      </div>
    </div>

    <!-- Step 7: After Metrics -->
    <div class="spine-step" :class="stepState(7)">
      <div class="step-marker" aria-hidden="true">7</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">7. After Metrics</strong>
          <span v-if="!e.afterMetrics" class="step-status tone-muted">Pending measurement</span>
          <span v-else class="step-status tone-good">Measured</span>
        </div>
        <div v-if="e.afterMetrics" class="baseline-metrics-row">
          <div class="base-metric">
            <span class="lbl">Visibility</span>
            <span class="val">{{ formatValue(getMetricVal(e.afterMetrics, 'visibility'), '%') }}</span>
          </div>
          <div class="base-metric">
            <span class="lbl">Citation Share</span>
            <span class="val">{{ formatValue(getMetricVal(e.afterMetrics, 'citation'), '%') }}</span>
          </div>
          <div class="base-metric">
            <span class="lbl">Competitor Share</span>
            <span class="val">{{ formatValue(getMetricVal(e.afterMetrics, 'competitor'), '%') }}</span>
          </div>
          <div class="base-metric">
            <span class="lbl">Prompt Volume</span>
            <span class="val">{{ formatValue(getMetricVal(e.afterMetrics, 'volume'), '') }}</span>
          </div>
        </div>
        <div v-else class="pending-metrics-row">
          <span class="placeholder-val">—</span>
          <span class="placeholder-val">—</span>
          <span class="placeholder-val">—</span>
          <span class="placeholder-val">—</span>
        </div>
      </div>
    </div>

    <!-- Step 8: Outcome -->
    <div class="spine-step" :class="stepState(8)">
      <div class="step-marker" aria-hidden="true">8</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">8. Outcome</strong>
          <StatusBadge v-if="e.displayStatus || isVerified" :label="e.displayStatus ?? experimentLabel(e.status)" :tone="experimentTone(e.status)" />
          <span v-else class="step-status tone-muted">Pending</span>
        </div>
        <p class="step-text dim">
          {{ e.outcomeLabel ? `Outcome: ${humanize(e.outcomeLabel)}${e.outcomeLabel === 'inconclusive' ? ` (${e.inconclusiveReason ?? 'reason not recorded'})` : ''}.` : 'Outcome will be determined after the verification window.' }}
        </p>
      </div>
    </div>

    <!-- Step 9: Learning -->
    <div class="spine-step" :class="stepState(9)">
      <div class="step-marker" aria-hidden="true">9</div>
      <div class="step-content">
        <div class="row spread">
          <strong class="step-title">9. Learning</strong>
          <span v-if="e.policyVersion" class="step-status tone-policy">{{ e.policyVersion }}</span>
        </div>
        <p class="step-text dim">
          {{ e.outcome ? (e.outcome.learningApplied ? 'The policy was updated from this result.' : 'No policy update was made from this result.') : 'A conclusive result will update the policy; an inconclusive one will not.' }}
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.causal-spine {
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: relative;
  padding-left: 30px;
}
.causal-spine::before {
  content: '';
  position: absolute;
  left: 12px;
  top: 18px;
  bottom: 18px;
  width: 2px;
  background: linear-gradient(180deg, var(--primary) 0%, rgba(99, 102, 241, 0.2) 100%);
}
.spine-step {
  position: relative;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 16px;
  transition: all var(--motion-normal) var(--ease-calm);
}
.step-marker {
  position: absolute;
  left: -29px;
  top: 14px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--bg-1);
  border: 2px solid var(--border);
  display: grid;
  place-items: center;
  font-size: 10px;
  font-weight: 700;
  color: var(--text-dim);
}
.spine-step.current {
  border-color: var(--primary);
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.1) 0%, rgba(15, 21, 38, 0.95) 100%);
  box-shadow: 0 0 20px rgba(99, 102, 241, 0.18);
  transform: translateZ(2px);
}
.spine-step.current .step-marker {
  border-color: var(--primary);
  background: var(--primary);
  color: #ffffff;
  box-shadow: 0 0 10px var(--primary-glow);
}
.spine-step.completed .step-marker {
  border-color: var(--good);
  background: var(--good-soft);
  color: var(--good);
}
.spine-step.future {
  opacity: 0.55;
}

.step-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.step-title {
  font-size: 13px;
  color: #ffffff;
  font-weight: 700;
}
.step-status {
  font-size: 11px;
  font-weight: 600;
}
.step-text {
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.4;
}
.bold-white {
  color: var(--text-primary);
  font-weight: 600;
}
.step-meta {
  font-size: 11px;
  color: var(--text-faint);
}
.view-changes-btn {
  font-size: 11px;
  padding: 2px 8px;
  background: rgba(99, 102, 241, 0.15);
  border: 1px solid rgba(99, 102, 241, 0.35);
  color: #818cf8;
}
.baseline-metrics-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 6px;
  background: var(--bg-1);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-subtle);
}
.base-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.base-metric .lbl {
  font-size: 10px;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.base-metric .val {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}
.pending-metrics-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 6px;
  background: var(--bg-1);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  text-align: center;
  color: var(--text-faint);
}
.placeholder-val {
  font-size: 14px;
}
.eligibility-box {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  margin-top: 4px;
}
.eligibility-text {
  font-size: 12px;
  color: #fde68a;
}
</style>
