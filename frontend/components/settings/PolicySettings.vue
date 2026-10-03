<script setup lang="ts">
import type { ApiErrorInfo, PolicyInfo } from '~/types'

// Reads policy state; allowed-action toggles write to PATCH /api/policy. Execution stays approval-gated server-side.
const props = defineProps<{ policy: PolicyInfo }>()
const emit = defineEmits<{ saved: [] }>()
const ALL = ['observe', 'update_existing_page', 'create_canonical_page', 'create_faq', 'create_comparison_content', 'publisher_outreach', 'structured_data']
const allowed = ref<Record<string, boolean>>({ ...(props.policy.allowedActions ?? {}) })
watch(() => props.policy.allowedActions, (v) => { allowed.value = { ...(v ?? {}) } })
const saving = ref(false)
const msg = ref<string | null>(null)
const err = ref<ApiErrorInfo | null>(null)
const reported = computed(() => props.policy.allowedActions !== undefined && props.policy.available !== false)

async function save() {
  saving.value = true; msg.value = null; err.value = null
  try {
    await apiFetch('/api/policy', { method: 'PATCH', body: { allowed_actions: allowed.value } })
    msg.value = 'Saved.'
    emit('saved')
  } catch (e) { err.value = e as ApiErrorInfo } finally { saving.value = false }
}
</script>

<template>
  <section class="stack" data-testid="policy-settings">
    <ErrorState v-if="policy.available === false" :error="{ kind: 'unavailable', message: policy.unavailableReason ?? 'Policy engine is unavailable.' }" surface="Policy engine" />
    <dl class="kv card">
      <dt>Current policy version</dt><dd>{{ policy.version ?? 'not reported' }}</dd>
      <dt>Learning mode</dt><dd>{{ policy.learningMode ? humanize(policy.learningMode) : 'not reported' }}</dd>
      <dt>Cold-start status</dt><dd>{{ policy.coldStart == null ? 'not reported' : policy.coldStart ? 'Cold-start priors in use' : 'Learned policy' }}</dd>
      <dt>Human approval required</dt><dd>{{ policy.requiresApproval == null ? 'not reported' : policy.requiresApproval ? 'Yes' : 'No' }}</dd>
      <dt>Minimum confidence</dt><dd>{{ policy.minConfidence ?? 'not reported' }}</dd>
    </dl>
    <fieldset class="card stack sm">
      <legend>Allowed actions</legend>
      <p v-if="!reported" class="dim">The API did not report allowed actions.</p>
      <label v-for="a in ALL" :key="a" class="row">
        <input v-model="allowed[a]" type="checkbox" :disabled="!reported || a === 'observe'">{{ actionLabel(a) }}<span v-if="a === 'observe'" class="meta">(always allowed)</span>
      </label>
      <div class="row"><button type="button" class="primary" :disabled="saving || !reported" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
        <span v-if="msg" class="tone-good" role="status">{{ msg }}</span></div>
      <ErrorState v-if="err" :error="err" surface="Policy settings" />
    </fieldset>
  </section>
</template>
