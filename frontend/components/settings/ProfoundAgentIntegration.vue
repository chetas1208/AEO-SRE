<script setup lang="ts">
const apiBase = useApiBase()
const endpointUrl = computed(() => `${apiBase}/api/change-checks`)
const copied = ref(false)

async function copyEndpoint() {
  try {
    await navigator.clipboard.writeText(endpointUrl.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // fallback
  }
}

const samplePayload = `{
  "org_id": "00000000-0000-0000-0000-000000000001",
  "agent": {
    "id": "profound-citation-agent-1",
    "name": "Citation Recovery Agent"
  },
  "profound_run_id": "run-8f2a1b9c",
  "source_mode": "SIMULATED",
  "target_url": "https://testco.example/enterprise/security",
  "action_type": "update_existing_page",
  "proposed_claims": [
    "SAML SSO is available on the Enterprise plan."
  ],
  "reason": "Recover citation share on authentication prompts",
  "expected_kpi": "citation_share",
  "risk": "low",
  "reversible": true
}`
</script>

<template>
  <section class="card stack sm profound-agent-integration" aria-label="Profound Agent Change Guard integration" data-testid="profound-agent-integration">
    <div class="row spread wrap">
      <div class="row" style="gap: 8px; align-items: center;">
        <span class="pill-endpoint">API ENDPOINT</span>
        <strong class="title">Profound Agent Change Guard</strong>
      </div>
      <span class="badge tone-good">Live Ingestion Path</span>
    </div>

    <p class="dim">
      Profound Agents in external workflows call this endpoint before applying changes. Change Guard evaluates collisions, active experiment contamination, and brand canonical truth.
    </p>

    <!-- Workflow Flow Card -->
    <div class="workflow-flow-card">
      <div class="flow-step">
        <span class="step-num">1</span>
        <span class="step-name">Profound Agent</span>
        <span class="step-desc">Generates ChangeSet</span>
      </div>
      <div class="flow-arrow">→</div>
      <div class="flow-step">
        <span class="step-num">2</span>
        <span class="step-name">Call API Node</span>
        <span class="step-desc">POST /api/change-checks</span>
      </div>
      <div class="flow-arrow">→</div>
      <div class="flow-step active">
        <span class="step-num">3</span>
        <span class="step-name">Change Guard</span>
        <span class="step-desc">Evaluates safety</span>
      </div>
      <div class="flow-arrow">→</div>
      <div class="flow-step">
        <span class="step-num">4</span>
        <span class="step-name">Decision Returned</span>
        <span class="step-desc">ALLOW / DELAY / BLOCK</span>
      </div>
    </div>

    <!-- Endpoint URL & Copy Button -->
    <div class="endpoint-box">
      <span class="method">POST</span>
      <code class="url">{{ endpointUrl }}</code>
      <button type="button" class="copy-btn" @click="copyEndpoint">
        {{ copied ? 'Copied!' : 'Copy endpoint' }}
      </button>
    </div>

    <!-- Sample Payload Accordion -->
    <details class="payload-details">
      <summary class="meta">View sample ChangeSet request payload (SIMULATED AGENT)</summary>
      <pre class="payload-code"><code>{{ samplePayload }}</code></pre>
    </details>
  </section>
</template>

<style scoped>
.profound-agent-integration {
  background: rgba(15, 21, 38, 0.85);
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: var(--radius);
  padding: 16px;
}
.pill-endpoint {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.05em;
  padding: 2px 7px;
  border-radius: 4px;
  background: rgba(99, 102, 241, 0.2);
  color: #a5b4fc;
  border: 1px solid rgba(99, 102, 241, 0.4);
}
.title {
  font-size: 15px;
  color: #ffffff;
}
.workflow-flow-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  background: rgba(10, 14, 26, 0.7);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  margin-top: 8px;
  overflow-x: auto;
}
.flow-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 2px;
  min-width: 90px;
}
.flow-step .step-num {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--bg-1);
  border: 1px solid var(--border);
  display: grid;
  place-items: center;
  font-size: 10px;
  font-weight: 700;
  color: var(--text-dim);
}
.flow-step.active .step-num {
  background: var(--primary);
  color: #ffffff;
  border-color: var(--primary);
}
.step-name {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-primary);
}
.step-desc {
  font-size: 10px;
  color: var(--text-faint);
}
.flow-arrow {
  color: var(--text-faint);
  font-weight: 700;
  font-size: 14px;
}
.endpoint-box {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(10, 14, 26, 0.9);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  margin-top: 6px;
}
.method {
  font-size: 11px;
  font-weight: 800;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(16, 185, 129, 0.2);
  color: #6ee7b7;
  border: 1px solid rgba(16, 185, 129, 0.4);
}
.url {
  flex: 1;
  font-family: monospace;
  font-size: 12px;
  color: #e2e8f0;
}
.copy-btn {
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 4px;
}
.payload-details {
  margin-top: 6px;
}
.payload-code {
  margin: 6px 0 0;
  padding: 10px;
  background: rgba(10, 14, 26, 0.95);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-family: monospace;
  font-size: 11px;
  color: #94a3b8;
  overflow-x: auto;
}
</style>
