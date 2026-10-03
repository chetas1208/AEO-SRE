<script setup lang="ts">
import type { EvidenceItem } from '~/types'

const props = defineProps<{
  evidenceSnapshot?: Array<{ id?: string; title?: string; excerpt?: string | null; url?: string | null }> | null
  diff?: string | null
}>()

defineEmits<{
  viewDiff: []
}>()

const count = computed(() => {
  let c = props.evidenceSnapshot?.length ?? 0
  if (props.diff) c += 1
  return Math.max(3, c)
})
</script>

<template>
  <section class="card stack sm provenance-card" aria-label="Evidence and provenance">
    <div class="row spread">
      <h3>Evidence &amp; Provenance</h3>
      <span class="count-badge">{{ count }}</span>
    </div>

    <div class="pills-row">
      <button v-if="props.diff" class="artifact-pill" type="button" @click="$emit('viewDiff')">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <polyline points="16 18 22 12 16 6" />
          <polyline points="8 6 2 12 8 18" />
        </svg>
        <span>Updated page (diff)</span>
      </button>

      <div class="artifact-pill disabled">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <span>SERP snapshot</span>
      </div>

      <div class="artifact-pill disabled">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        <span>LLM answer</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.provenance-card {
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 14px;
}
.count-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(99, 102, 241, 0.2);
  color: #818cf8;
  border: 1px solid rgba(99, 102, 241, 0.4);
}
.pills-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.artifact-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(15, 21, 38, 0.8);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 5px 10px;
  font-size: 11px;
  color: var(--blue);
  cursor: pointer;
  transition: all var(--motion-fast) var(--ease-calm);
}
.artifact-pill:hover:not(.disabled) {
  background: var(--surface-hover);
  border-color: var(--border-strong);
  color: #ffffff;
}
.artifact-pill.disabled {
  cursor: default;
  color: var(--text-dim);
  opacity: 0.85;
}
</style>
