<script setup lang="ts">
import type { PromptRow } from '~/types'

defineProps<{ prompts: PromptRow[]; unavailableReason?: string | null }>()
const INTENT: Record<string, string> = { high: 'High intent', medium: 'Medium intent', low: 'Low intent' }
</script>

<template>
  <div v-if="prompts.length" style="overflow-x: auto">
    <table data-testid="prompt-table">
      <thead>
        <tr>
          <th scope="col">Prompt</th><th scope="col">Intent</th><th scope="col">Volume</th><th scope="col">Our visibility</th>
          <th scope="col">Competitor</th><th scope="col">Engine(s)</th><th scope="col">Change</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in prompts" :key="p.id">
          <td>
            <details>
              <summary>{{ p.text }}</summary>
              <dl class="kv" style="margin-top: 6px">
                <dt>Persona</dt><dd>{{ p.persona ?? 'not reported' }}</dd>
                <dt>Region</dt><dd>{{ p.region ?? 'not reported' }}</dd>
                <dt>Previous visibility</dt><dd>{{ formatValue(p.visibilityBefore, 'pp') }}</dd>
                <dt>Citations</dt>
                <dd><ul v-if="p.citations?.length" style="margin: 0; padding-left: 16px"><li v-for="c in p.citations" :key="c">{{ c }}</li></ul><span v-else class="dim">none reported</span></dd>
                <dt>Observed</dt><dd><DataFreshness :at="p.observedAt" /></dd>
              </dl>
            </details>
          </td>
          <td>{{ p.intent ? (INTENT[p.intent] ?? humanize(p.intent)) : '—' }}</td>
          <td>{{ p.volume != null ? formatValue(p.volume) : '—' }}</td>
          <td>{{ formatValue(p.visibility, 'pp') }}</td>
          <td>{{ p.competitor ? `${p.competitor}${p.competitorVisibility != null ? ' ' + formatValue(p.competitorVisibility, 'pp') : ''}` : '—' }}</td>
          <td>{{ p.engines?.length ? p.engines.join(', ') : '—' }}</td>
          <td>{{ p.change != null ? formatDeltaValue(p.change, p.unit ?? 'pp') : '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
  <ErrorState v-else-if="unavailableReason" :error="{ kind: 'unavailable', message: unavailableReason }" surface="Affected prompts" />
  <EmptyState v-else title="No affected prompts were reported for this incident." />
</template>
