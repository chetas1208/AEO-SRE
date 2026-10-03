<script setup lang="ts">
import type { CanonicalClaim } from '~/types'

// Settings -> Organization -> "Canonical truth". Claims are admin-managed per organization. Writes carry X-Actor (human).
// Retire is a soft retire (status=retired); nothing is deleted. The server validates; no client-side judgement.
const props = defineProps<{ orgId: string }>()
const claims = useCanonicalClaims(() => props.orgId)
const editingId = ref<string | 'new' | null>(null)
const form = reactive({ statement: '', entities: '', scope: '', validFrom: '', source: '' })
const list = computed(() => claims.data.value ?? [])
const active = computed(() => list.value.filter((c) => c.status !== 'retired'))
const retired = computed(() => list.value.filter((c) => c.status === 'retired'))

function startNew() { Object.assign(form, { statement: '', entities: '', scope: '', validFrom: '', source: '' }); editingId.value = 'new' }
function startEdit(c: CanonicalClaim) {
  Object.assign(form, { statement: c.statement, entities: c.entities.join(', '), scope: c.scope ?? '', validFrom: c.validFrom?.slice(0, 10) ?? '', source: c.source ?? '' })
  editingId.value = c.id
}
async function save() {
  const draft = { statement: form.statement.trim(), entities: form.entities.split(',').map((x) => x.trim()).filter(Boolean), scope: form.scope.trim(), validFrom: form.validFrom, source: form.source.trim() }
  const ok = editingId.value === 'new' ? await claims.add(draft) : await claims.edit(editingId.value as string, draft)
  if (ok) editingId.value = null
}
async function retire(c: CanonicalClaim) { await claims.retire(c.id) }
</script>

<template>
  <section class="card stack" aria-label="Canonical truth" data-testid="canonical-truth">
    <div class="row spread wrap">
      <h2>Canonical truth</h2>
      <button type="button" class="primary" :disabled="editingId !== null" data-testid="claim-add" @click="startNew">Add claim</button>
    </div>
    <p class="dim">Statements that are true for this organization. Agent changes whose claims contradict them are blocked by the Change Guard.</p>
    <label class="stack sm" style="max-width: 320px"><span class="meta">Acting as (recorded on every change)</span><input v-model="claims.actor.value" aria-label="Acting as"></label>

    <LoadingState v-if="claims.pending.value && !claims.data.value" message="Loading canonical claims…" />
    <ErrorState v-else-if="claims.error.value && !claims.data.value" :error="claims.error.value" surface="Canonical claims" @retry="claims.refresh()" />
    <template v-else>
      <EmptyState v-if="!active.length && editingId !== 'new'" title="No canonical claims defined." :lines="['The semantic contradiction check is skipped until at least one claim exists. Change checks will report skipped_no_canonical_truth, not a pass.']" />
      <ul v-else class="stack sm" style="list-style: none; padding: 0; margin: 0">
        <li v-for="c in active" :key="c.id" class="card stack sm" data-testid="claim-row">
          <p><strong>{{ c.statement }}</strong></p>
          <p class="meta">Entities: {{ c.entities.length ? c.entities.join(', ') : 'unavailable' }} · Scope: {{ c.scope ?? 'unavailable' }} · Valid from: {{ c.validFrom ? absoluteTime(c.validFrom) : 'unavailable' }} · Source: {{ c.source ?? 'unavailable' }}</p>
          <div class="row"><button type="button" :disabled="editingId !== null" @click="startEdit(c)">Edit</button><button type="button" class="danger" :disabled="claims.submitting.value" @click="retire(c)">Retire</button></div>
        </li>
      </ul>

      <form v-if="editingId" class="stack sm card selected" data-testid="claim-form" @submit.prevent="save">
        <label class="stack sm"><span class="meta">Statement</span><textarea v-model="form.statement" rows="2" required /></label>
        <label class="stack sm"><span class="meta">Entities (comma separated)</span><input v-model="form.entities"></label>
        <label class="stack sm"><span class="meta">Scope</span><input v-model="form.scope"></label>
        <label class="stack sm"><span class="meta">Valid from</span><input v-model="form.validFrom" type="date"></label>
        <label class="stack sm"><span class="meta">Source / provenance</span><input v-model="form.source"></label>
        <div class="row"><button type="submit" class="primary" :disabled="claims.submitting.value || !form.statement.trim()">{{ claims.submitting.value ? 'Saving…' : editingId === 'new' ? 'Add claim' : 'Save claim' }}</button><button type="button" @click="editingId = null">Cancel</button></div>
      </form>
      <ErrorState v-if="claims.writeError.value" :error="claims.writeError.value" surface="Canonical claim change" />

      <details v-if="retired.length">
        <summary>Retired claims ({{ retired.length }})</summary>
        <ul class="stack sm" style="padding-left: 18px"><li v-for="c in retired" :key="c.id" class="dim">{{ c.statement }} <span class="badge tone-muted">Retired</span></li></ul>
      </details>
    </template>
  </section>
</template>
