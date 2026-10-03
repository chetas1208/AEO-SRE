<script setup lang="ts">
import type { ExperimentList } from '~/types'

const props = defineProps<{ list: ExperimentList }>()

const counts = computed(() => {
  const items = props.list.items || []
  const active = items.filter(e => ['proposed', 'approved', 'executing', 'executed', 'awaiting_verification'].includes(e.status)).length
  const awaiting = items.filter(e => e.status === 'awaiting_verification').length
  const completed = items.filter(e => e.status === 'rewarded' || e.status === 'verified' || e.reward != null).length

  // Protected targets: active experiments currently locking target surfaces
  const protectedCount = items.filter(e => ['executing', 'executed', 'awaiting_verification'].includes(e.status)).length

  return { active, protectedTargets: protectedCount, awaiting, completed }
})
</script>

<template>
  <div class="summary-cards-grid" data-testid="experiment-summary">
    <!-- Active Experiments -->
    <div class="summary-card">
      <div class="card-icon tone-primary">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M10 2v7.31M14 2v7.31M8.5 2h7M14 9.3a6.5 6.5 0 1 1-4 0" />
          <path d="M5.52 16h12.96" />
        </svg>
      </div>
      <div class="card-content">
        <div class="val">{{ counts.active }}</div>
        <div class="title">Active</div>
        <div class="sub">Currently running or measuring</div>
      </div>
    </div>

    <!-- Protected Targets -->
    <div class="summary-card">
      <div class="card-icon tone-info">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
      </div>
      <div class="card-content">
        <div class="val">{{ counts.protectedTargets }}</div>
        <div class="title">Protected Targets</div>
        <div class="sub">Guarded against change collisions</div>
      </div>
    </div>

    <!-- Awaiting Verification -->
    <div class="summary-card">
      <div class="card-icon tone-warn">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      </div>
      <div class="card-content">
        <div class="val">{{ counts.awaiting }}</div>
        <div class="title">Awaiting Verification</div>
        <div class="sub">In measurement window</div>
      </div>
    </div>

    <!-- Completed -->
    <div class="summary-card">
      <div class="card-icon tone-good">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
          <polyline points="22 4 12 14.01 9 11.01" />
        </svg>
      </div>
      <div class="card-content">
        <div class="val">{{ counts.completed }}</div>
        <div class="title">Completed</div>
        <div class="sub">Finished with causal results</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.summary-cards-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
.summary-card {
  background: var(--surface-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
  transition: all var(--motion-fast) var(--ease-calm);
}
.summary-card:hover {
  border-color: var(--border-strong);
  background: var(--surface-hover);
}
.card-icon {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  display: grid;
  place-items: center;
  flex-shrink: 0;
  border: 1px solid var(--border);
}
.card-icon.tone-primary {
  background: rgba(99, 102, 241, 0.12);
  color: #818cf8;
  border-color: rgba(99, 102, 241, 0.3);
}
.card-icon.tone-warn {
  background: rgba(245, 158, 11, 0.12);
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.3);
}
.card-icon.tone-good {
  background: rgba(16, 185, 129, 0.12);
  color: #34d399;
  border-color: rgba(16, 185, 129, 0.3);
}
.card-icon.tone-policy {
  background: rgba(168, 85, 247, 0.12);
  color: #c084fc;
  border-color: rgba(168, 85, 247, 0.3);
}
.card-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.card-content .val {
  font-size: 20px;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: -0.02em;
  line-height: 1.2;
}
.card-content .title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.card-content .sub {
  font-size: 11px;
  color: var(--text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 1100px) {
  .summary-cards-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
