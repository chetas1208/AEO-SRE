<script setup lang="ts">
import type { GNode } from '~/types/graph'
import { inspectorRows, nodeTitle } from '~/utils/graph'

// Node inspector: real node properties only (ids, timestamps, source, source_mode). SIMULATED nodes are badged.
defineProps<{ node: GNode }>()
defineEmits<{ close: [] }>()
</script>

<template>
  <aside class="card stack sm" aria-label="Graph node inspector" data-testid="graph-node-inspector" :data-node-id="node.id">
    <div class="row spread">
      <div class="row wrap" style="gap: 8px">
        <strong>{{ nodeTitle(node) }}</strong>
        <span v-if="node.sourceMode === 'SIMULATED'" class="badge tone-policy" data-testid="simulated-badge">SIMULATED</span>
      </div>
      <button type="button" class="link" @click="$emit('close')">Close</button>
    </div>
    <dl class="kv">
      <template v-for="r in inspectorRows(node)" :key="r.k"><dt>{{ r.k }}</dt><dd class="mono" style="word-break: break-all">{{ r.v }}</dd></template>
    </dl>
  </aside>
</template>
