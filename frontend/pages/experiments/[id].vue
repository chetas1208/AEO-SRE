<script setup lang="ts">
const route = useRoute()
const id = computed(() => String(route.params.id))
const { data: e, pending, error, refresh } = useExperiment(id)
const diffDialogOpen = ref(false)

const awaiting = computed(() => {
  return !!e.value?.awaitingReward && !['rejected', 'failed'].includes(e.value?.status ?? '')
})

const code = computed(() => {
  if (!e.value) return ''
  return e.value.code ?? `EXP-${e.value.id.slice(0, 4)}`
})

const provenance = computed<'live' | 'test' | 'replay'>(() => {
  const s = (e.value?.source ?? '').toLowerCase()
  if (s.includes('test') || s.includes('fixture')) return 'test'
  if (s.includes('replay')) return 'replay'
  if (s.includes('live') || s.includes('profound')) return 'live'
  return 'test'
})
</script>

<template>
  <div class="stack" :key="id">
    <LoadingState v-if="pending && !e" message="Loading experiment record…" />
    <ErrorState v-else-if="error && !e" :error="error" surface="Experiment" @retry="refresh()" />
    <template v-else-if="e">
      <NuxtLink to="/experiments" class="meta back-link">← All experiments</NuxtLink>

      <!-- Experiment Detail Header -->
      <header class="exp-detail-header">
        <div class="row spread wrap">
          <div class="stack xs">
            <div class="row wrap" style="gap: 8px; align-items: center;">
              <span class="exp-header-code">{{ code }}</span>
              <span class="pill-provenance" :class="provenance">{{ provenance.toUpperCase() }}</span>
            </div>
            <h2 class="exp-header-title">{{ e.incidentTitle ?? 'Intervention Experiment' }}</h2>
            <div class="exp-meta-line meta">
              <span>Incident: <NuxtLink v-if="e.incidentId" :to="`/incidents/${e.incidentId}`">{{ e.incidentTitle ?? e.incidentId }}</NuxtLink><span v-else>not reported</span></span>
              <span class="sep">·</span>
              <span>Started <span :title="absoluteTime(e.startedAt)">{{ shortDate(e.startedAt) }}</span></span>
            </div>
          </div>

          <div class="row" style="gap: 10px;">
            <NuxtLink v-if="e.incidentId" :to="`/incidents/${e.incidentId}`" class="view-incident-btn">
              View Incident →
            </NuxtLink>
          </div>
        </div>

        <!-- 4 Info Badges Row -->
        <div class="info-badges-strip">
          <div class="badge-block">
            <span class="b-lbl">Intervention</span>
            <div class="b-val row">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tone-info">
                <path d="M10 2v7.31M14 2v7.31M8.5 2h7M14 9.3a6.5 6.5 0 1 1-4 0" />
                <path d="M5.52 16h12.96" />
              </svg>
              <span>{{ e.actionTitle ?? actionLabel(e.action) }}</span>
            </div>
          </div>

          <div class="badge-block">
            <span class="b-lbl">Status</span>
            <div class="b-val row">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tone-warn">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
              <span>{{ e.displayStatus ?? experimentLabel(e.status) }}</span>
            </div>
          </div>

          <div class="badge-block">
            <span class="b-lbl">Source</span>
            <div class="b-val row">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tone-info">
                <polygon points="12 2 2 7 12 12 22 7 12 2" />
              </svg>
              <span style="text-transform: capitalize;">{{ provenance }}</span>
            </div>
          </div>

          <div class="badge-block">
            <span class="b-lbl">Policy Version</span>
            <div class="b-val row">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tone-policy">
                <polygon points="12 2 2 7 12 12 22 7 12 2" />
                <polyline points="2 17 12 22 22 17" />
              </svg>
              <span>{{ e.policyVersion ?? 'unavailable' }}</span>
            </div>
          </div>
        </div>
      </header>

      <ProtectionBanner :protection="e.protection" />

      <!-- 2-Column Main Workspace -->
      <div class="analysis-grid" style="gap: 20px;">
        <!-- Left: 9-Step Causal Spine -->
        <div class="stack" style="gap: 16px;">
          <ExperimentHypothesis :spec="e.spec" :metrics="e.declaredMetrics" />
          <ExperimentSpine :experiment="e" @view-changes="diffDialogOpen = true" />
        </div>

        <!-- Right: Primary Metrics, Policy Learning, Confounders, Provenance -->
        <div class="stack" style="gap: 16px;">
          <ExperimentOutcome
            :before="e.beforeMetrics"
            :after="e.afterMetrics"
            :awaiting="awaiting"
            :dry-run="e.dryRun"
          />

          <ExperimentVerdict :experiment="e" />

          <RewardBreakdown :reward="e.rewardData" :status="e.status" :dry-run="e.dryRun" />

          <PolicySnapshot :experiment="e" />

          <PotentialConfounders :confounders="e.outcome ? e.outcome.confounders : null" />

          <EvidenceProvenance
            :diff="e.proposedChange?.diff || e.package?.diff || e.actualChange"
            :evidence-snapshot="e.evidenceSnapshot"
            @view-diff="diffDialogOpen = true"
          />
        </div>
      </div>

      <div class="card">
        <ChangeChecksPanel :experiment-code="code" />
        <p v-if="!e.protection" class="meta" data-testid="protection-unavailable">Protection status unavailable for this experiment.</p>
      </div>

      <!-- Diff / Changes Modal Dialog -->
      <dialog :open="diffDialogOpen" class="diff-dialog" aria-labelledby="diff-dialog-title">
        <div class="dlg">
          <div class="row spread">
            <h3 id="diff-dialog-title">Intervention Package / Code Diff</h3>
            <button class="link" type="button" @click="diffDialogOpen = false">Close</button>
          </div>
          <p class="meta dim">Verified proposed change payload before execution.</p>
          <pre v-if="e.deviation && e.actualChange">{{ e.actualChange }}</pre>
          <pre v-else-if="e.proposedChange?.diff">{{ e.proposedChange.diff }}</pre>
          <pre v-else-if="e.package?.diff">{{ e.package.diff }}</pre>
          <p v-else class="dim">No code diff was recorded for this intervention.</p>
        </div>
      </dialog>
    </template>
    <LoadingState v-else message="Loading experiment record…" />
  </div>
</template>

<style scoped>
.exp-detail-header {
  padding: 8px 0 16px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 4px;
}
.exp-header-code {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-dim);
}
.exp-header-title {
  font-size: 20px;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: -0.02em;
}
.exp-meta-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.sep {
  color: var(--text-faint);
}
.view-incident-btn {
  background: rgba(15, 21, 38, 0.85);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 7px 14px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  transition: all var(--motion-fast) var(--ease-calm);
}
.view-incident-btn:hover {
  background: var(--surface-hover);
  border-color: var(--border-strong);
}

.info-badges-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-top: 14px;
  padding: 10px 14px;
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.badge-block {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.b-lbl {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-faint);
}
.b-val {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.diff-dialog {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 100;
  width: min(800px, 94vw);
}
</style>
