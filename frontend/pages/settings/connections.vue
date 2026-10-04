<script setup lang="ts">
const rows = ref<any[]>([])
const err = ref('')

async function load() {
  try {
    rows.value = await apiFetch('/api/oauth/connections/muse')
  } catch (e: any) {
    err.value = e?.data?.error?.message || 'Not signed in'
  }
}

async function revoke(id: string) {
  await apiFetch(`/api/oauth/connections/muse/${id}/revoke`, { method: 'POST' })
  await load()
}

onMounted(load)
</script>

<template>
  <div class="stack">
    <h1>Connections</h1>
    <section class="card stack">
      <h2>Muse</h2>
      <p class="meta">Muse can access only the Profound Lift data you approve.</p>
      <EmptyState v-if="!rows.length && !err" title="Not connected" :lines="['Connect from Muse using OAuth, or start OAuth from your Muse client.']" />
      <div v-for="r in rows" :key="r.authorization_id" class="stack sm">
        <div>Connected · scopes: {{ r.scopes.join(', ') }}</div>
        <div class="meta">Since {{ r.connected_at }}</div>
        <button type="button" @click="revoke(r.authorization_id)">Disconnect Muse</button>
      </div>
      <p v-if="err" class="tone-bad">{{ err }} <NuxtLink to="/login">Sign in</NuxtLink></p>
    </section>
    <NuxtLink to="/settings">← Settings</NuxtLink>
  </div>
</template>
