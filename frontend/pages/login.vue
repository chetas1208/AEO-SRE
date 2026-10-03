<script setup lang="ts">
const email = ref('')
const password = ref('')
const err = ref('')
const router = useRouter()
async function submit() {
  err.value = ''
  try {
    await apiFetch('/api/auth/login', {
      method: 'POST',
      body: { email: email.value, password: password.value }
    })
    await router.push('/')
  } catch (e: any) {
    err.value = e?.data?.error?.message || 'Login failed'
  }
}
</script>

<template>
  <div class="stack card" style="max-width: 420px; margin: 2rem auto">
    <h1>Sign in</h1>
    <form class="stack" @submit.prevent="submit">
      <label class="stack sm"><span class="meta">Email</span><input v-model="email" type="email" required></label>
      <label class="stack sm"><span class="meta">Password</span><input v-model="password" type="password" required></label>
      <button class="primary" type="submit">Sign in</button>
      <p v-if="err" class="tone-bad">{{ err }}</p>
    </form>
    <NuxtLink to="/signup">Create account</NuxtLink>
  </div>
</template>
