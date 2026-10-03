<script setup lang="ts">
const props = withDefaults(defineProps<{
  proposedClaim?: string
  canonicalStatement?: string
  agentName?: string
  canonicalSource?: string
  evidenceExcerpt?: string
  evidenceUrl?: string
}>(), {
  proposedClaim: 'SAML is available only on Enterprise plans.',
  canonicalStatement: 'SAML SSO is available on both Business and Enterprise plans.',
  agentName: 'Citation Recovery Agent',
  canonicalSource: 'Brand Security Docs',
  evidenceExcerpt: 'All Business and Enterprise tiers include SAML 2.0 single sign-on integration at no additional cost.',
  evidenceUrl: 'https://testco.example/enterprise/security'
})
</script>

<template>
  <section class="card stack sm claim-conflict-view" aria-label="Canonical truth conflict comparison" data-testid="claim-conflict-view">
    <div class="row spread">
      <div class="row" style="gap: 8px; align-items: center;">
        <span class="conflict-badge">CONTRADICTION DETECTED</span>
        <strong class="title">Canonical Brand Truth Conflict</strong>
      </div>
      <span class="badge tone-bad">High Confidence</span>
    </div>

    <p class="dim">
      The proposed change makes an assertion that directly contradicts this organization's registered canonical brand truth.
    </p>

    <!-- Side-by-side comparison -->
    <div class="comparison-grid">
      <div class="card claim-card proposed">
        <div class="claim-label">PROPOSED CLAIM</div>
        <p class="claim-text">“{{ proposedClaim }}”</p>
        <div class="claim-meta">
          <span>Submitted by {{ agentName }}</span>
        </div>
      </div>

      <div class="vs-divider" aria-hidden="true">
        <span>VS</span>
      </div>

      <div class="card claim-card canonical">
        <div class="claim-label">CANONICAL TRUTH</div>
        <p class="claim-text">“{{ canonicalStatement }}”</p>
        <div class="claim-meta">
          <span>Source: {{ canonicalSource }}</span>
        </div>
      </div>
    </div>

    <!-- Underlying evidence -->
    <div v-if="evidenceExcerpt" class="evidence-box">
      <div class="row" style="gap: 6px;">
        <span class="meta dim">Verified Evidence Excerpt:</span>
        <a v-if="evidenceUrl" :href="evidenceUrl" target="_blank" rel="noopener noreferrer" class="link meta">{{ evidenceUrl }}</a>
      </div>
      <blockquote class="excerpt">{{ evidenceExcerpt }}</blockquote>
    </div>
  </section>
</template>

<style scoped>
.claim-conflict-view {
  background: rgba(15, 21, 38, 0.85);
  border: 1px solid rgba(244, 63, 94, 0.4);
  border-left: 4px solid var(--bad);
  border-radius: var(--radius);
  padding: 16px;
}
.conflict-badge {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.05em;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(244, 63, 94, 0.2);
  color: #fda4af;
  border: 1px solid rgba(244, 63, 94, 0.5);
}
.title {
  font-size: 14px;
  color: #ffffff;
}
.comparison-grid {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 12px;
  margin-top: 8px;
}
@media (max-width: 768px) {
  .comparison-grid {
    grid-template-columns: 1fr;
  }
  .vs-divider {
    justify-content: center;
  }
}
.claim-card {
  padding: 14px;
  border-radius: var(--radius-sm);
  background: rgba(10, 14, 26, 0.9);
}
.claim-card.proposed {
  border: 1px solid rgba(244, 63, 94, 0.35);
}
.claim-card.canonical {
  border: 1px solid rgba(6, 182, 212, 0.35);
}
.claim-label {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-faint);
  margin-bottom: 6px;
}
.claim-card.proposed .claim-label {
  color: #fda4af;
}
.claim-card.canonical .claim-label {
  color: #67e8f9;
}
.claim-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.4;
  margin-bottom: 8px;
}
.claim-meta {
  font-size: 11px;
  color: var(--text-faint);
}
.vs-divider {
  display: flex;
  align-items: center;
  font-size: 11px;
  font-weight: 800;
  color: var(--text-faint);
  padding: 0 4px;
}
.evidence-box {
  margin-top: 6px;
  padding: 10px 12px;
  background: rgba(10, 14, 26, 0.6);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
}
.excerpt {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-dim);
  font-style: italic;
  line-height: 1.4;
  border-left: 2px solid var(--border-strong);
  padding-left: 8px;
}
</style>
