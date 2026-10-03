<script setup lang="ts">
defineProps<{ open?: boolean }>()
defineEmits<{ navigate: [] }>()

const route = useRoute()
const matches = useMatches()
const discoveryGaps = useDiscoveryGaps()
const experiments = useExperiments()
const { campaigns } = useCampaigns()

const matchesCount = computed(() => matches.intents.value.length)
const gapsCount = computed(() => discoveryGaps.criticalGapsCount.value || discoveryGaps.gaps.value.length)
const waitingExperiments = computed(() => waitingCount(experiments.data.value))
const campaignsCount = computed(() => campaigns.value.length)

const isMatches = computed(() => route.path.startsWith('/matches'))
const isCampaigns = computed(() => route.path.startsWith('/campaigns'))
const isDiscoveryGaps = computed(() => route.path.startsWith('/discovery-gaps') || route.path.startsWith('/incidents'))
const isExperiments = computed(() => route.path.startsWith('/experiments'))
</script>

<template>
  <aside :class="['rail', { open }]" aria-label="Primary navigation">
    <div class="logo">
      <svg class="logo-mark" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <linearGradient id="agentmatch-grad" x1="2" y1="2" x2="30" y2="30" gradientUnits="userSpaceOnUse">
            <stop stop-color="#38bdf8" />
            <stop offset="0.5" stop-color="#818cf8" />
            <stop offset="1" stop-color="#c084fc" />
          </linearGradient>
          <filter id="logo-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>
        <g filter="url(#logo-glow)">
          <path d="M16 4 L26 10 L26 22 L16 28 L6 22 L6 10 Z" stroke="url(#agentmatch-grad)" stroke-width="2" fill="none" opacity="0.8" />
          <circle cx="16" cy="16" r="4" fill="url(#agentmatch-grad)" />
          <path d="M16 4 L16 12 M26 22 L19 18 M6 22 L13 18" stroke="url(#agentmatch-grad)" stroke-width="1.5" stroke-linecap="round" />
        </g>
      </svg>
      <div class="logo-text">
        <span class="logo-title">AgentMatch</span>
        <span class="logo-sub">Agent-Native AI Discovery</span>
      </div>
    </div>

    <nav>
      <NuxtLink to="/matches" :class="{ active: isMatches }" @click="$emit('navigate')">
        <span class="row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <circle cx="12" cy="12" r="6" />
            <circle cx="12" cy="12" r="2" />
          </svg>
          AgentMatch
        </span>
        <span v-if="matchesCount" class="nav-badge tone-info" :aria-label="`${matchesCount} active intent envelopes`">{{ matchesCount }}</span>
      </NuxtLink>

      <NuxtLink to="/campaigns" :class="{ active: isCampaigns }" @click="$emit('navigate')">
        <span class="row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <line x1="12" y1="1" x2="12" y2="23" />
            <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
          </svg>
          Campaigns
        </span>
        <span v-if="campaignsCount" class="nav-badge tone-purple" :aria-label="`${campaignsCount} campaigns tracked`">{{ campaignsCount }}</span>
      </NuxtLink>

      <NuxtLink to="/experiments" :class="{ active: isExperiments }" @click="$emit('navigate')">
        <span class="row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M10 2v7.31M14 2v7.31M8.5 2h7M14 9.3a6.5 6.5 0 1 1-4 0" />
            <path d="M5.52 16h12.96" />
          </svg>
          Experiments
        </span>
        <span v-if="waitingExperiments" class="nav-badge tone-warn" :aria-label="`${waitingExperiments} awaiting verification`">{{ waitingExperiments }}</span>
      </NuxtLink>

      <NuxtLink to="/discovery-gaps" :class="{ active: isDiscoveryGaps }" @click="$emit('navigate')">
        <span class="row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          Discovery Gaps
        </span>
        <span v-if="gapsCount" class="nav-badge tone-bad" :aria-label="`${gapsCount} discovery gaps need attention`">{{ gapsCount }}</span>
      </NuxtLink>
    </nav>

    <div class="bottom">
      <!-- Agent connector indicators: Muse & Profound -->
      <div class="connectors-panel" aria-label="Connector Status">
        <div class="connector-item">
          <div class="row">
            <span class="pulse-dot tone-info" aria-hidden="true" />
            <span class="connector-name">Muse</span>
          </div>
          <span class="connector-tag">Intent Live</span>
        </div>
        <div class="connector-item">
          <div class="row">
            <span class="pulse-dot tone-purple" aria-hidden="true" />
            <span class="connector-name">Profound</span>
          </div>
          <span class="connector-tag">Perception Sync</span>
        </div>
      </div>

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
.nav-badge.tone-info {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.4);
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
.connectors-panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: rgba(15, 23, 42, 0.5);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
}
.connector-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
}
.connector-name {
  color: var(--fg-muted);
  font-weight: 600;
}
.connector-tag {
  color: var(--fg-dim);
  font-size: 10px;
  font-family: var(--font-mono);
}
.pulse-dot.tone-purple {
  background: #a855f7;
  box-shadow: 0 0 6px rgba(168, 85, 247, 0.6);
}
</style>
