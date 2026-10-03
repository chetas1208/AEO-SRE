<script setup lang="ts">
import type { IncidentEvent } from '~/types'
import type { StreamState } from '~/stores/liveSystem'

// Backend messages are rendered verbatim. Failed steps stay visible.
defineProps<{ events: IncidentEvent[]; state: StreamState }>()
defineEmits<{ reconnect: [] }>()
</script>

<template>
  <section class="card stack sm" aria-label="Investigation timeline" data-testid="timeline">
    <div class="row spread">
      <h3>Investigation timeline</h3>
      <span class="meta">
        <template v-if="state === 'connected'">Stream connected</template>
        <template v-else-if="state === 'connecting'">Connecting…</template>
        <template v-else-if="state === 'disconnected'">Stream disconnected · <button type="button" class="link" @click="$emit('reconnect')">Reconnect</button></template>
        <template v-else>Stream idle</template>
      </span>
    </div>
    <p v-if="!events.length" class="dim">{{ state === 'connected' ? 'No investigation steps recorded yet.' : state === 'disconnected' ? 'Event stream unavailable; no steps received.' : 'Waiting for investigation events…' }}</p>
    <ol v-else class="timeline">
      <li v-for="e in events" :key="e.id" :class="`tone-${stepTone(e.status)}`">
        <time class="mono faint" :datetime="e.timestamp" :title="absoluteTime(e.timestamp)">{{ clockTime(e.timestamp) }}</time>
        <span class="dot" aria-hidden="true" />
        <span>
          <span class="dim" style="color: var(--text)">{{ e.message }}</span>
          <span :class="['badge', `tone-${stepTone(e.status)}`]" style="margin-left: 6px">{{ e.status }}</span>
          <span class="meta"> {{ humanize(e.stage) }}</span>
        </span>
      </li>
    </ol>
  </section>
</template>
