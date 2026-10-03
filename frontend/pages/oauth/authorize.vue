<script setup lang="ts">
const route = useRoute()
const state = computed(() => String(route.query.oauth_state || ''))
const pending = ref<any>(null)
const readOnly = ref(true)
const err = ref('')

onMounted(async () => {
  if (!state.value) return
  pending.value = await apiFetch('/api/oauth/pending', { query: { oauth_state: state.value } })
})

const scopes = computed(() => readOnly.value
  ? ['matches:read', 'discovery_gaps:read', 'campaigns:read', 'experiments:read']
  : [...(pending.value?.scopes || [])])

async function approve(approved: boolean) {
  err.value = ''
  const orgId = pending.value?.organization_id // user picks in full UX; default first workspace via /api/auth/me
  let organization_id = orgId
  if (!organization_id) {
    const me = await apiFetch<any>('/api/auth/me')
    organization_id = me.workspaces?.[0]?.organization_id
  }
  try {
    const res = await apiFetch<{ redirect_to: string }>('/api/oauth/consent', {
      method: 'POST',
      body: { oauth_state: state.value, organization_id, approved, scopes: scopes.value }
    })
    window.location.href = res.redirect_to
  } catch (e: any) {
    err.value = e?.data?.error?.message || 'Consent failed — sign in first'
  }
}
</script>

<template>
  <div class="stack card" style="max-width: 520px; margin: 2rem auto">
    <h1>Connect Muse to AgentMatch</h1>
    <p v-if="!state" class="tone-bad">Missing oauth_state.</p>
    <template v-else-if="pending">
      <p><strong>{{ pending.client_name }}</strong> is requesting access:</p>
      <ul>
        <li v-for="s in scopes" :key="s">{{ pending.scope_labels?.[s] || s }}</li>
      </ul>
      <label><input v-model="readOnly" type="checkbox"> Read only (recommended)</label>
      <div class="row">
        <button class="primary" type="button" @click="approve(true)">Authorize</button>
        <button type="button" @click="approve(false)">Cancel</button>
      </div>
      <p v-if="err" class="tone-bad">{{ err }} <NuxtLink to="/login">Sign in</NuxtLink></p>
    </template>
    <LoadingState v-else message="Loading consent…" />
  </div>
</template>
