<script setup lang="ts">
import type { GExplanation, GraphView } from '~/types/graph'
import { nodeTitle } from '~/utils/graph'

// Deterministic textual alternative to the graph: the API's explanation statements, each with its supporting node ids.
// Hover/focus a statement to highlight exactly those nodes in the topology.
const props = defineProps<{ explanation: GExplanation | null | undefined; view: GraphView | null | undefined; active?: number | null }>()
const emit = defineEmits<{ hover: [index: number | null] }>()
const byId = computed(() => new Map((props.view?.nodes ?? []).map((n) => [n.id, n])))
const label = (id: string) => { const n = byId.value.get(id); return n ? `${nodeTitle(n)} (${n.label})` : id }
</script>

<template>
  <section class="stack sm" aria-label="Why this decision" data-testid="graph-explanation">
    <h4>Why?</h4>
    <p v-if="!explanation || explanation.unavailable" class="dim" data-testid="graph-explanation-unavailable">Explanation unavailable. The graph could not produce one<template v-if="explanation?.reason"> ({{ explanation.reason }})</template>.</p>
    <p v-else-if="explanation.found === false" class="dim">{{ explanation.text ?? 'This change is not in the graph yet.' }}</p>
    <template v-else>
      <p v-if="explanation.stale" class="tone-warn meta">The graph is stale; these statements may be out of date.</p>
      <p v-if="!explanation.statements.length" class="dim">{{ explanation.text ?? 'No explanation statements were reported.' }}</p>
      <ol v-else class="stack sm" style="margin: 0; padding-left: 20px">
        <li
          v-for="(s, i) in explanation.statements" :key="i" tabindex="0" data-testid="graph-statement" :data-active="active === i"
          @mouseenter="emit('hover', i)" @mouseleave="emit('hover', null)" @focus="emit('hover', i)" @blur="emit('hover', null)"
        >
          <p>{{ s.text }}</p>
          <p class="meta">Supported by:
            <span v-for="(id, k) in s.nodeIds" :key="id" class="mono" :title="id">{{ label(id) }}<template v-if="!byId.has(id)"> [not in current view]</template><template v-if="k < s.nodeIds.length - 1">; </template></span>
            <span v-if="!s.nodeIds.length" class="dim">no node ids reported</span>
          </p>
        </li>
      </ol>
    </template>
  </section>
</template>
