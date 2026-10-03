<script setup lang="ts">
import type { IntegrationStatus } from '~/types'

// Connection state only. Secret values are never rendered; only the NAMES of missing server env vars are shown.
defineProps<{ integration: IntegrationStatus }>()
</script>

<template>
  <section class="card stack sm" :aria-label="integration.label" data-testid="integration-card">
    <div class="row spread"><h3>{{ integration.label }}</h3>
      <StatusBadge
        :label="integration.state === 'healthy' ? (integration.kind === 'executor' ? 'Available' : 'Connected') : integration.state === 'degraded' ? 'Degraded' : integration.optional ? 'Optional · not configured' : 'Not connected'"
        :tone="integration.state === 'healthy' ? 'good' : integration.state === 'degraded' ? 'warn' : 'muted'"
      />
    </div>
    <dl class="kv">
      <dt>Last successful request</dt><dd><DataFreshness v-if="integration.lastSuccess" :at="integration.lastSuccess" /><span v-else class="dim">none recorded</span></dd>
      <dt>Last error</dt><dd>{{ integration.lastError ?? 'none reported' }}</dd>
      <dt v-if="integration.detail">Detail</dt><dd v-if="integration.detail">{{ integration.detail }}</dd>
      <dt v-if="integration.missingFields.length">{{ integration.optional ? 'Optional server config' : 'Missing server config' }}</dt><dd v-if="integration.missingFields.length" class="mono">{{ integration.missingFields.join(', ') }}</dd>
    </dl>
    <p v-if="integration.kind === 'executor'" class="meta">Executor. {{ integration.optional ? 'Optional: the manual executor is always used unless an operator selects this one.' : 'The default for every action.' }}</p>
    <p v-if="integration.missingFields.length" class="meta">Configure via the server environment. Secret values are never shown.</p>
  </section>
</template>
