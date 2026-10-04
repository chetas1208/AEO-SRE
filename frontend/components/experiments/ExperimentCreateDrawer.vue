<script setup lang="ts">
import type { ExperimentCreateIn, ExperimentDetail } from '~/types'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    initialTrigger?: 'gap' | 'campaign' | 'direct'
    initialName?: string
    initialHypothesis?: string
    initialAction?: string
    initialTargetUrl?: string
    initialPrimaryMetric?: string
    initialIncidentId?: string
    initialCampaignId?: string
  }>(),
  {
    modelValue: false,
    initialTrigger: 'direct',
    initialName: '',
    initialHypothesis: '',
    initialAction: 'update_existing_page',
    initialTargetUrl: '',
    initialPrimaryMetric: 'visibility',
    initialIncidentId: undefined,
    initialCampaignId: undefined,
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'created', detail: ExperimentDetail): void
}>()

const router = useRouter()
const { createExperiment, isCreating, createError } = useExperiments()

const form = reactive<{
  name: string
  hypothesis: string
  trigger: 'gap' | 'campaign' | 'direct'
  action: string
  targetUrl: string
  targetKey: string
  primaryMetric: string
  verificationHours: number
  campaignId: string
  incidentId: string
  autoActivate: boolean
}>({
  name: '',
  hypothesis: '',
  trigger: 'direct',
  action: 'update_existing_page',
  targetUrl: '',
  targetKey: '',
  primaryMetric: 'visibility',
  verificationHours: 48,
  campaignId: '',
  incidentId: '',
  autoActivate: true,
})

const mixpanelMetricOptions = ref<Array<{ value: string; label: string; expected: string }>>([])
const baselinePreview = ref<{ status?: string; metrics?: Record<string, number>; primary_value?: number | null } | null>(null)
const baselineLoading = ref(false)

async function loadBaselinePreview() {
  baselineLoading.value = true
  try {
    const org = useOrganizationStore()
    const q: Record<string, string> = { primary_metric: form.primaryMetric }
    if (org.currentId) q.org_id = org.currentId
    baselinePreview.value = await apiFetch('/api/experiments/baseline-preview', { query: q })
  } catch {
    baselinePreview.value = { status: 'ERROR', metrics: {} }
  } finally {
    baselineLoading.value = false
  }
}

async function loadMixpanelCatalog() {
  try {
    const cat = await apiFetch<{ metrics?: Array<{ id: string; label: string }> }>('/api/integrations/mixpanel/catalog')
    mixpanelMetricOptions.value = (cat.metrics ?? []).map((m) => ({
      value: m.id,
      label: `Mixpanel · ${m.label}`,
      expected: 'event count',
    }))
  } catch {
    mixpanelMetricOptions.value = []
  }
}

// Sync props to form on open
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      form.trigger = props.initialTrigger
      form.name = props.initialName || (props.initialTargetUrl ? `Verification: ${props.initialTargetUrl.split('/').pop()}` : 'Canonical Truth Verification')
      form.hypothesis = props.initialHypothesis || ''
      form.action = props.initialAction || 'update_existing_page'
      form.targetUrl = props.initialTargetUrl || ''
      form.targetKey = props.initialTargetUrl ? `target:${props.initialTargetUrl.trim().toLowerCase().replace(/\/$/, '')}` : ''
      form.primaryMetric = props.initialPrimaryMetric || 'visibility'
      form.campaignId = props.initialCampaignId || ''
      form.incidentId = props.initialIncidentId || ''
      form.verificationHours = 48
      form.autoActivate = true
      void loadMixpanelCatalog()
      void loadBaselinePreview()
    }
  },
  { immediate: true }
)

watch(
  () => form.primaryMetric,
  () => {
    if (props.modelValue) void loadBaselinePreview()
  }
)

const ACTION_OPTIONS = [
  { value: 'update_existing_page', label: 'Update Existing Page (Recommended)' },
  { value: 'create_canonical_page', label: 'Create Canonical Landing Page' },
  { value: 'create_faq', label: 'Create Technical FAQ Section' },
  { value: 'structured_data', label: 'Deploy JSON-LD Structured Evidence' },
  { value: 'publisher_outreach', label: 'Publisher & Index Outreach' },
  { value: 'observe', label: 'Observe Only (Passive Monitoring)' },
]

const PROFOUND_METRICS = [
  { value: 'visibility', label: 'Brand Visibility (Profound Score)', expected: 'increase' },
  { value: 'citation_share', label: 'Official Citation Share (%)', expected: 'increase' },
  { value: 'accuracy', label: 'Perceived Accuracy / Truth Fit', expected: 'increase' },
  { value: 'competitor_share', label: 'Competitor Share of Voice', expected: 'decrease' },
]

const METRIC_OPTIONS = computed(() => [...PROFOUND_METRICS, ...mixpanelMetricOptions.value])

const WINDOW_OPTIONS = [
  { hours: 24, label: '24 Hours (Fast verification)' },
  { hours: 48, label: '48 Hours (Standard cycle)' },
  { hours: 168, label: '7 Days (Deep LLM re-crawl)' },
  { hours: 336, label: '14 Days (Full index update)' },
]

const step = ref<1 | 2 | 3>(1)

function close() {
  emit('update:modelValue', false)
  step.value = 1
}

async function submit() {
  if (!form.name.trim() || !form.hypothesis.trim()) {
    return
  }

  const payload: ExperimentCreateIn = {
    name: form.name.trim(),
    hypothesis: form.hypothesis.trim(),
    selected_action: form.action,
    target_url: form.targetUrl.trim() || null,
    target_key: form.targetKey.trim() || (form.targetUrl.trim() ? `target:${form.targetUrl.trim().toLowerCase().replace(/\/$/, '')}` : null),
    primary_metric: form.primaryMetric,
    verification_window_hours: form.verificationHours,
    incident_id: form.incidentId || null,
    campaign_id: form.campaignId || null,
    auto_activate: form.autoActivate,
  }

  try {
    const detail = await createExperiment(payload)
    emit('created', detail)
    close()
    if (detail?.id) {
      router.push(`/experiments/${detail.id}`)
    }
  } catch {
    // Handled by createError in composable
  }
}
</script>

<template>
  <div v-if="modelValue" class="drawer-backdrop" @click.self="close">
    <aside class="drawer-panel" role="dialog" aria-modal="true" aria-label="Create New Experiment">
      <!-- Drawer Header -->
      <div class="drawer-header">
        <div class="header-titles">
          <div class="badge-row">
            <span class="step-badge">Step {{ step }} of 3</span>
            <span v-if="form.trigger === 'gap'" class="trigger-pill">Trigger: Discovery Gap</span>
            <span v-else-if="form.trigger === 'campaign'" class="trigger-pill">Trigger: Campaign</span>
            <span v-else class="trigger-pill">Trigger: Direct Initiative</span>
          </div>
          <h2 class="title">Create Real Experiment</h2>
          <p class="subtitle">Deploy verified causal A/B testing backed by Change Guard protection</p>
        </div>
        <button class="close-btn" aria-label="Close drawer" @click="close">✕</button>
      </div>

      <!-- Error Banner -->
      <div v-if="createError" class="error-banner" role="alert">
        <span class="error-icon">⚠️</span>
        <div class="error-content">
          <strong>Experiment creation blocked:</strong>
          <p>{{ createError }}</p>
        </div>
      </div>

      <!-- Drawer Body: Stepped Form -->
      <div class="drawer-body">
        <!-- STEP 1: Core Hypothesis & Target -->
        <div v-if="step === 1" class="step-content">
          <div class="form-group">
            <label for="exp-name" class="form-label">
              Experiment Name <span class="required">*</span>
            </label>
            <input
              id="exp-name"
              v-model="form.name"
              type="text"
              class="form-input"
              placeholder="e.g. Enterprise SAML Canonical Pricing Clarification"
              required
            />
          </div>

          <div class="form-group">
            <label for="exp-hypothesis" class="form-label">
              Causal Hypothesis <span class="required">*</span>
              <span class="label-hint">What change are we testing, and why will metrics move?</span>
            </label>
            <textarea
              id="exp-hypothesis"
              v-model="form.hypothesis"
              class="form-textarea"
              rows="3"
              placeholder="e.g. Publishing verified SAML matrix on enterprise docs will displace stale 2024 reviews and increase official citation share by 15pp within 48h."
              required
            ></textarea>
          </div>

          <div class="form-group">
            <label for="exp-action" class="form-label">Intervention Action</label>
            <select id="exp-action" v-model="form.action" class="form-select">
              <option v-for="opt in ACTION_OPTIONS" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>

          <div class="form-group">
            <label for="exp-url" class="form-label">
              Target URL / Scope
              <span class="label-hint">Page protected from conflicting edits during measurement</span>
            </label>
            <input
              id="exp-url"
              v-model="form.targetUrl"
              type="url"
              class="form-input font-mono"
              placeholder="https://docs.example.com/security/saml-sso"
            />
          </div>
        </div>

        <!-- STEP 2: Metrics & Verification Window -->
        <div v-else-if="step === 2" class="step-content">
          <div class="form-group">
            <label for="exp-metric" class="form-label">Primary Success Metric</label>
            <select id="exp-metric" v-model="form.primaryMetric" class="form-select">
              <option v-for="m in METRIC_OPTIONS" :key="m.value" :value="m.value">
                {{ m.label }} (Expected: {{ m.expected }})
              </option>
            </select>
          </div>

          <div class="baseline-preview-card">
            <div class="baseline-header">
              <span class="pulse-indicator">●</span>
              <span>Baseline at creation (not editable)</span>
            </div>
            <p class="baseline-honesty">
              On submit, the backend measures pre-window baselines from live Profound signals
              <template v-if="mixpanelMetricOptions.length"> and Mixpanel event counts</template>.
              You cannot type fake numbers.
            </p>
            <div v-if="form.primaryMetric.startsWith('mixpanel:')" class="baseline-grid">
              <div class="baseline-item">
                <span class="baseline-label">Mixpanel metric</span>
                <span class="baseline-val font-mono">{{ form.primaryMetric }}</span>
              </div>
            </div>
            <div v-else class="baseline-grid">
              <div class="baseline-item">
                <span class="baseline-label">Primary metric</span>
                <span class="baseline-val font-mono">{{ form.primaryMetric }}</span>
              </div>
              <div class="baseline-item">
                <span class="baseline-label">Live baseline</span>
                <span v-if="baselineLoading" class="baseline-val">Syncing…</span>
                <span v-else-if="baselinePreview?.primary_value != null" class="baseline-val">
                  {{ baselinePreview.primary_value <= 1.5 ? (baselinePreview.primary_value * 100).toFixed(2) + '%' : baselinePreview.primary_value }}
                </span>
                <span v-else class="baseline-val tone-warn">No signals — run Profound sync</span>
              </div>
              <div class="baseline-item">
                <span class="baseline-label">Source</span>
                <span class="baseline-val">{{ baselinePreview?.status === 'OK' ? 'Profound LIVE' : (baselinePreview?.status ?? '—') }}</span>
              </div>
            </div>
            <p class="baseline-note">Captured live from backend telemetry. Never fabricated or manually inflated.</p>
          </div>

          <div class="form-group">
            <label for="exp-window" class="form-label">Verification Window</label>
            <select id="exp-window" v-model="form.verificationHours" class="form-select">
              <option v-for="w in WINDOW_OPTIONS" :key="w.hours" :value="w.hours">
                {{ w.label }}
              </option>
            </select>
          </div>

          <div class="protection-notice">
            <div class="notice-icon">🛡️</div>
            <div class="notice-body">
              <strong>Change Guard Target Protection</strong>
              <p>
                Incoming agent actions or PRs targeting
                <code>{{ form.targetUrl || 'this prompt cluster' }}</code>
                will be blocked or sent for human review to prevent experiment contamination.
              </p>
            </div>
          </div>
        </div>

        <!-- STEP 3: Review & Finalize -->
        <div v-else class="step-content">
          <div class="review-card">
            <div class="review-row">
              <span class="review-label">Experiment:</span>
              <strong class="review-value">{{ form.name }}</strong>
            </div>
            <div class="review-row">
              <span class="review-label">Action:</span>
              <span class="review-badge">{{ form.action }}</span>
            </div>
            <div class="review-row">
              <span class="review-label">Primary Metric:</span>
              <span class="review-value font-mono">{{ form.primaryMetric }}</span>
            </div>
            <div class="review-row">
              <span class="review-label">Protected Target:</span>
              <span class="review-value font-mono">{{ form.targetUrl || 'cluster-wide' }}</span>
            </div>
            <div class="review-row">
              <span class="review-label">Window:</span>
              <span class="review-value">{{ form.verificationHours }} hours</span>
            </div>
            <div class="review-divider"></div>
            <div class="review-hypothesis">
              <span class="review-label">Hypothesis Statement:</span>
              <p class="hypothesis-quote">“{{ form.hypothesis }}”</p>
            </div>
          </div>

          <div class="activation-toggle">
            <label class="toggle-label">
              <input v-model="form.autoActivate" type="checkbox" class="toggle-checkbox" />
              <span class="toggle-text">
                <strong>Activate Immediately</strong>
                <small>Issue execution package and begin measurement window right away</small>
              </span>
            </label>
          </div>
        </div>
      </div>

      <!-- Drawer Footer Actions -->
      <div class="drawer-footer">
        <button v-if="step > 1" class="btn-secondary" :disabled="isCreating" @click="step--">
          ← Back
        </button>
        <button v-else class="btn-secondary" :disabled="isCreating" @click="close">
          Cancel
        </button>

        <button
          v-if="step < 3"
          class="btn-primary"
          :disabled="!form.name.trim() || !form.hypothesis.trim()"
          @click="step++"
        >
          Next Step →
        </button>
        <button
          v-else
          class="btn-primary-action"
          :disabled="isCreating"
          @click="submit"
        >
          <span v-if="isCreating" class="spinner">⏳ Creating...</span>
          <span v-else>🚀 Create Experiment</span>
        </button>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(10, 15, 29, 0.75);
  backdrop-filter: blur(4px);
  z-index: var(--z-modal, 50);
  display: flex;
  justify-content: flex-end;
}

.drawer-panel {
  width: 100%;
  max-width: 520px;
  height: 100vh;
  background: #0f172a;
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  display: flex;
  flex-direction: column;
  box-shadow: -10px 0 30px rgba(0, 0, 0, 0.5);
  animation: slideIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slideIn {
  from {
    transform: translateX(100%);
  }
  to {
    transform: translateX(0);
  }
}

.drawer-header {
  padding: 20px 24px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.badge-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}

.step-badge {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  background: rgba(56, 189, 248, 0.15);
  color: #38bdf8;
  padding: 2px 8px;
  border-radius: 4px;
}

.trigger-pill {
  font-size: 11px;
  font-weight: 500;
  background: rgba(255, 255, 255, 0.06);
  color: #94a3b8;
  padding: 2px 8px;
  border-radius: 4px;
}

.title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: #f8fafc;
}

.subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: #94a3b8;
}

.close-btn {
  background: transparent;
  border: none;
  color: #64748b;
  font-size: 18px;
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  transition: color 0.15s;
}
.close-btn:hover {
  color: #f8fafc;
}

.error-banner {
  margin: 16px 24px 0;
  padding: 12px 16px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 8px;
  display: flex;
  gap: 12px;
  color: #fca5a5;
  font-size: 13px;
}

.error-content p {
  margin: 2px 0 0;
  color: #fecaca;
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.form-group {
  margin-bottom: 20px;
}

.form-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #e2e8f0;
  margin-bottom: 6px;
}

.required {
  color: #f43f5e;
}

.label-hint {
  display: block;
  font-size: 12px;
  font-weight: 400;
  color: #64748b;
  margin-top: 2px;
}

.form-input,
.form-textarea,
.form-select {
  width: 100%;
  background: #1e293b;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  padding: 10px 14px;
  color: #f8fafc;
  font-size: 14px;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.form-input:focus,
.form-textarea:focus,
.form-select:focus {
  outline: none;
  border-color: #38bdf8;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2);
}

.baseline-preview-card {
  background: #1e293b;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 20px;
}

.baseline-header {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  color: #94a3b8;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
}

.pulse-indicator {
  color: #10b981;
  font-size: 10px;
}

.baseline-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.baseline-item {
  background: rgba(15, 23, 42, 0.6);
  padding: 8px 12px;
  border-radius: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.baseline-label {
  font-size: 12px;
  color: #94a3b8;
}

.baseline-val {
  font-size: 13px;
  font-weight: 700;
  color: #38bdf8;
  font-family: monospace;
}
.baseline-val.tone-warn {
  color: #fbbf24;
  font-size: 11px;
  font-weight: 600;
}

.baseline-note {
  font-size: 11px;
  color: #64748b;
  margin: 10px 0 0;
}

.protection-notice {
  background: rgba(56, 189, 248, 0.08);
  border: 1px solid rgba(56, 189, 248, 0.2);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.notice-body {
  font-size: 12px;
  color: #bae6fd;
}

.notice-body strong {
  display: block;
  color: #38bdf8;
  margin-bottom: 2px;
}

.review-card {
  background: #1e293b;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 20px;
}

.review-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  font-size: 13px;
}

.review-label {
  color: #94a3b8;
}

.review-value {
  color: #f8fafc;
}

.review-badge {
  background: rgba(99, 102, 241, 0.2);
  color: #818cf8;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-family: monospace;
}

.review-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.08);
  margin: 12px 0;
}

.hypothesis-quote {
  margin: 6px 0 0;
  font-size: 13px;
  font-style: italic;
  color: #cbd5e1;
  line-height: 1.4;
}

.activation-toggle {
  background: #1e293b;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 14px 16px;
}

.toggle-label {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  cursor: pointer;
}

.toggle-checkbox {
  margin-top: 3px;
  width: 16px;
  height: 16px;
  accent-color: #38bdf8;
}

.toggle-text strong {
  display: block;
  font-size: 13px;
  color: #f8fafc;
}

.toggle-text small {
  display: block;
  font-size: 12px;
  color: #64748b;
  margin-top: 2px;
}

.drawer-footer {
  padding: 16px 24px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  background: #0f172a;
}

.btn-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #cbd5e1;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-secondary:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.1);
  color: #f8fafc;
}

.btn-primary {
  background: #0284c7;
  border: 1px solid #38bdf8;
  color: #ffffff;
  padding: 8px 18px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-primary:hover:not(:disabled) {
  background: #0369a1;
}

.btn-primary-action {
  background: #10b981;
  border: 1px solid #34d399;
  color: #ffffff;
  padding: 8px 20px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-primary-action:hover:not(:disabled) {
  background: #059669;
}

.btn-primary:disabled,
.btn-primary-action:disabled,
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
