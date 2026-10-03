<script setup lang="ts">
const props = defineProps<{
  confounders?: Array<string | { kind: string; detail?: string | null; hard?: boolean | null }> | null
}>()

const assessed = computed(() => props.confounders != null)
const hasConfounders = computed(() => (props.confounders?.length ?? 0) > 0)
const text = (c: string | { kind: string; detail?: string | null; hard?: boolean | null }) =>
  typeof c === 'string' ? c : `${humanize(c.kind)}${c.detail ? `: ${c.detail}` : ''}${c.hard ? ' (makes the outcome unattributable)' : ''}`
</script>

<template>
  <section class="card stack xs confounders-card" aria-label="Potential confounders">
    <div class="row" style="gap: 8px;">
      <svg v-if="assessed && !hasConfounders" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tone-good" aria-hidden="true">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
        <polyline points="22 4 12 14.01 9 11.01" />
      </svg>
      <svg v-else-if="assessed" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="tone-warn" aria-hidden="true">
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
      <strong :class="hasConfounders ? 'tone-warn' : 'tone-good'" class="conf-title">
        {{ !assessed ? 'Confounders: not assessed yet' : hasConfounders ? 'Potential confounders detected' : 'No confounders detected in persisted data' }}
      </strong>
    </div>
    <p v-if="!assessed" class="conf-desc meta dim">Unavailable until the experiment is measured.</p>
    <p v-else-if="!hasConfounders" class="conf-desc meta dim">
      Only confounders visible in stored data are checked; absence of data is not proof of absence.
    </p>
    <ul v-else class="meta tone-warn" style="margin: 4px 0 0; padding-left: 18px;">
      <li v-for="(c, k) in props.confounders" :key="k">{{ text(c) }}</li>
    </ul>
  </section>
</template>

<style scoped>
.confounders-card {
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 14px;
}
.conf-title {
  font-size: 13px;
  font-weight: 600;
}
.conf-desc {
  font-size: 11px;
  line-height: 1.4;
  padding-left: 24px;
}
</style>
