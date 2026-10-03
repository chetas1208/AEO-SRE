<script setup lang="ts">
import type { SystemHealth } from '~/types'

const props = defineProps<{ health: SystemHealth }>()
const rows = computed(() => SYSTEM_COMPONENTS.map((c) => ({ label: c.label, cap: findCapability(props.health.capabilities, c.match) })))
const extra = computed(() => props.health.capabilities.filter((c) => !SYSTEM_COMPONENTS.some((s) => s.match.test(c.name))))
const tone = (s?: string) => (s === 'healthy' ? 'good' : s === 'degraded' ? 'warn' : s === 'unavailable' ? 'bad' : 'muted')
</script>

<template>
  <section class="stack" data-testid="system-health">
    <table>
      <thead><tr><th scope="col">Component</th><th scope="col">State</th><th scope="col">Detail</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.label">
          <th scope="row">{{ r.label }}</th>
          <td><StatusBadge :label="r.cap ? humanize(r.cap.state) : 'Not reported'" :tone="tone(r.cap?.state)" /></td>
          <td class="dim">{{ r.cap?.lastError ?? r.cap?.detail ?? '' }}</td>
        </tr>
        <tr v-for="c in extra" :key="c.name">
          <th scope="row">{{ c.label ?? humanize(c.name) }}</th><td><StatusBadge :label="humanize(c.state)" :tone="tone(c.state)" /></td><td class="dim">{{ c.lastError ?? c.detail ?? '' }}</td>
        </tr>
      </tbody>
    </table>
    <section v-if="health.executors?.length" class="card stack sm" aria-label="Executors" data-testid="executors">
      <h3>Executors</h3>
      <ul class="stack sm" style="list-style: none; padding: 0; margin: 0">
        <li v-for="x in health.executors" :key="x.name" class="row spread">
          <span>{{ x.label ?? humanize(x.name) }}<span v-if="x.detail" class="meta"> · {{ x.detail }}</span></span>
          <StatusBadge :label="x.state === 'healthy' ? 'Available' : 'Not configured'" :tone="x.state === 'healthy' ? 'good' : 'muted'" />
        </li>
      </ul>
    </section>
    <dl class="kv card">
      <dt>Last ingestion</dt><dd><DataFreshness :at="health.lastIngestionAt" /></dd>
      <dt>Last investigation</dt><dd><DataFreshness :at="health.lastInvestigationAt" /></dd>
      <dt>Last policy update</dt><dd><DataFreshness :at="health.lastPolicyUpdateAt" /></dd>
      <dt>Policy version</dt><dd>{{ health.policyVersion ?? 'not reported' }}</dd>
      <dt>Model artifact version</dt><dd>{{ health.modelArtifactVersion ?? 'not reported' }}</dd>
    </dl>
  </section>
</template>
