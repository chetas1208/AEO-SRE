<script setup lang="ts">
defineProps<{ open?: boolean }>()
defineEmits<{ navigate: [] }>()
const incidents = useIncidents()
const experiments = useExperiments()
const attention = computed(() => attentionCount(incidents.data.value))
const waiting = computed(() => waitingCount(experiments.data.value))
const route = useRoute()

const isIncidents = computed(() => route.path.startsWith('/incidents'))
const isExperiments = computed(() => route.path.startsWith('/experiments'))
const isSettings = computed(() => route.path.startsWith('/settings'))
</script>

<template>
  <aside :class="['rail', { open }]" aria-label="Primary navigation">
    <div class="logo">
      <svg class="logo-mark" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <linearGradient id="logo-grad" x1="2" y1="2" x2="30" y2="30" gradientUnits="userSpaceOnUse">
            <stop stop-color="#38bdf8" />
            <stop offset="0.5" stop-color="#6366f1" />
            <stop offset="1" stop-color="#a855f7" />
          </linearGradient>
          <filter id="logo-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>
        <g filter="url(#logo-glow)">
          <path d="M16 3 L19 12 L28 12 L21 17 L24 26 L16 21 L8 26 L11 17 L4 12 L13 12 Z" fill="url(#logo-grad)" opacity="0.9" />
          <circle cx="16" cy="16" r="3" fill="#ffffff" />
        </g>
      </svg>
      <div class="logo-text">
        <span class="logo-title">Change Guard</span>
        <span class="logo-sub">Cross-Agent Control</span>
      </div>
    </div>

    <nav>
      <NuxtLink to="/incidents" :class="{ active: isIncidents }" @click="$emit('navigate')">
        <span class="row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
          Change Guard
        </span>
        <span v-if="attention" class="nav-badge tone-bad" :aria-label="`${attention} changes need attention`">{{ attention }}</span>
      </NuxtLink>

      <NuxtLink to="/experiments" :class="{ active: isExperiments }" @click="$emit('navigate')">
        <span class="row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M10 2v7.31M14 2v7.31M8.5 2h7M14 9.3a6.5 6.5 0 1 1-4 0" />
            <path d="M5.52 16h12.96" />
          </svg>
          Experiments
        </span>
        <span v-if="waiting" class="nav-badge tone-warn" :aria-label="`${waiting} awaiting verification`">{{ waiting }}</span>
      </NuxtLink>

      <NuxtLink to="/settings" :class="{ active: isSettings }" @click="$emit('navigate')">
        <span class="row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
          Settings
        </span>
      </NuxtLink>
    </nav>

    <div class="bottom">
      <SystemStatus />
      <OrganizationSwitcher />
    </div>
  </aside>
</template>

<style scoped>
.nav-badge {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}
.nav-badge.tone-bad {
  background: rgba(244, 63, 94, 0.25);
  color: #fda4af;
  border: 1px solid rgba(244, 63, 94, 0.4);
}
.nav-badge.tone-warn {
  background: rgba(245, 158, 11, 0.25);
  color: #fde68a;
  border: 1px solid rgba(245, 158, 11, 0.4);
}
</style>
