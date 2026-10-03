<script setup lang="ts">
const form = reactive({ email: '', password: '', display_name: '', workspace_name: '', domain: '' })
const err = ref('')
const router = useRouter()

async function submit() {
  err.value = ''
  try {
    await apiFetch('/api/auth/signup', { method: 'POST', body: form })
    await router.push('/')
  } catch (e: any) {
    err.value = e?.data?.error?.message || 'Signup failed'
  }
}
</script>

<template>
  <div class="stack card" style="max-width: 480px; margin: 2rem auto">
    <h1>Create account</h1>
    <form class="stack" @submit.prevent="submit">
      <label class="stack sm"><span class="meta">Email</span><input v-model="form.email" type="email" required></label>
      <label class="stack sm"><span class="meta">Password (10+ chars)</span><input v-model="form.password" type="password" required minlength="10"></label>
      <label class="stack sm"><span class="meta">Display name</span><input v-model="form.display_name"></label>
      <label class="stack sm"><span class="meta">Workspace name</span><input v-model="form.workspace_name"></label>
      <label class="stack sm"><span class="meta">Primary domain</span><input v-model="form.domain" placeholder="example.com" required></label>
      <button class="primary" type="submit">Create account</button>
      <p v-if="err" class="tone-bad">{{ err }}</p>
    </form>
    <NuxtLink to="/login">Sign in</NuxtLink>
  </div>
</template>
