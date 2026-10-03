<script setup lang="ts">
import type { ExperimentProtection } from '~/types'

// "Protected until <time>" banner, shown only when the server says protection.protected === true.
// Unknown (no `protection` object) renders nothing here; the panel below states "unavailable".
const props = defineProps<{ protection: ExperimentProtection | null | undefined }>()
const until = computed(() => props.protection?.until ?? null)
function utc(iso: string | null): string {
  if (!iso) return 'unavailable'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'unavailable'
  return `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`
}
</script>

<template>
  <div v-if="protection?.protected" class="card protect-banner" role="status" data-testid="protection-banner">
    <div class="row wrap spread">
      <div class="row wrap" style="gap: 8px">
        <span class="badge tone-warn" data-testid="protected-badge"><span aria-hidden="true">‖</span> PROTECTED</span>
        <strong data-testid="protected-until">Protected until {{ utc(until) }}</strong>
        <span v-if="until" class="meta">({{ relativeUntil(until) }})</span>
      </div>
      <span v-if="protection.checksBlockedCount != null" class="meta">{{ protection.checksBlockedCount }} change check{{ protection.checksBlockedCount === 1 ? '' : 's' }} held</span>
    </div>
    <p class="dim">Other agents' changes to these targets are delayed until this experiment finishes measuring.<template v-if="protection.targets.length"> Targets: <span class="mono">{{ protection.targets.join(', ') }}</span></template></p>
  </div>
</template>

<style scoped>
.protect-banner { border-color: rgba(245, 158, 11, 0.45); background: var(--warn-soft); display: grid; gap: 6px; }
</style>
