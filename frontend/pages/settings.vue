<script setup lang="ts">
import type { ApiErrorInfo } from '~/types'

type Tab = 'organization' | 'integrations' | 'policy' | 'system'
const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'organization', label: 'Organization' }, { key: 'integrations', label: 'Integrations' }, { key: 'policy', label: 'Policy' }, { key: 'system', label: 'System' }
]
const tab = ref<Tab>('organization')
const org = useOrganizationStore()
const health = useSystemHealth()
const policy = usePolicy()
const settings = useSettings()
const integrations = computed(() => normIntegrations(settings.data.value))

const lines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean)
const form = reactive({ name: '', competitors: '', canonical: '', personas: '', topics: '' })
watch(() => org.current, (o) => {
  form.name = o?.name ?? ''
  form.competitors = (o?.competitorDomains ?? []).join('\n')
  form.canonical = (o?.canonicalDomains ?? []).join('\n')
  form.personas = (o?.personas ?? []).join('\n')
  form.topics = (o?.topics ?? []).join('\n')
}, { immediate: true })

const saving = ref(false)
const msg = ref<string | null>(null)
const err = ref<ApiErrorInfo | null>(null)
async function save() {
  if (!org.current) return
  saving.value = true; msg.value = null; err.value = null
  try {
    const raw = await apiFetch(`/api/organizations/${encodeURIComponent(org.current.id)}`, {
      method: 'PATCH',
      body: { name: form.name, competitor_domains: lines(form.competitors), canonical_domains: lines(form.canonical), personas: lines(form.personas), topics: lines(form.topics) }
    })
    org.upsert(normOrganization(raw as any))
    msg.value = 'Saved.'
  } catch (e) { err.value = e as ApiErrorInfo } finally { saving.value = false }
}
</script>

<template>
  <div class="stack">
    <h1>Settings</h1>
    <div class="tabs" role="tablist" aria-label="Settings sections">
      <button v-for="t in TABS" :key="t.key" type="button" role="tab" :aria-selected="tab === t.key" @click="tab = t.key">{{ t.label }}</button>
    </div>

    <section v-if="tab === 'organization'" class="stack">
      <LoadingState v-if="!org.loaded" message="Loading organization…" />
      <ErrorState v-else-if="org.error && !org.current" :error="org.error" surface="Organization" @retry="org.load()" />
      <EmptyState v-else-if="!org.current" title="No organization configured." :lines="['Use “Add organization” in the navigation rail to start monitoring a domain.']" />
      <form v-else class="card stack" @submit.prevent="save">
        <label class="stack sm"><span class="meta">Organization name</span><input v-model="form.name"></label>
        <label class="stack sm"><span class="meta">Primary domain</span><input :value="org.current.domain" readonly aria-readonly="true"></label>
        <label class="stack sm"><span class="meta">Competitor domains (one per line)</span><textarea v-model="form.competitors" rows="3" /></label>
        <label class="stack sm"><span class="meta">Canonical documentation domains (one per line)</span><textarea v-model="form.canonical" rows="3" /></label>
        <label class="stack sm"><span class="meta">Priority personas (one per line)</span><textarea v-model="form.personas" rows="3" /></label>
        <label class="stack sm"><span class="meta">Priority topic groups (one per line)</span><textarea v-model="form.topics" rows="3" /></label>
        <div class="row"><button class="primary" type="submit" :disabled="saving">{{ saving ? 'Saving…' : 'Save organization' }}</button><span v-if="msg" class="tone-good" role="status">{{ msg }}</span></div>
        <ErrorState v-if="err" :error="err" surface="Organization settings" />
      </form>
      <CanonicalClaimsEditor v-if="org.current" :org-id="org.current.id" />
    </section>

    <section v-else-if="tab === 'integrations'" class="stack">
      <p><NuxtLink to="/settings/connections">Muse OAuth connections →</NuxtLink></p>
      <LoadingState v-if="settings.pending.value && !settings.data.value" message="Checking integrations…" />
      <ErrorState v-else-if="settings.error.value && !settings.data.value" :error="settings.error.value" surface="Integration status" @retry="settings.refresh()" />
      <EmptyState v-else-if="!integrations.length" title="The API reported no integrations." />
      <div v-else class="metric-strip" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))">
        <IntegrationCard v-for="i in integrations" :key="i.key" :integration="i" />
      </div>
      <ProfoundAgentIntegration />
    </section>

    <section v-else-if="tab === 'policy'">
      <LoadingState v-if="policy.pending.value && !policy.data.value" message="Loading policy…" />
      <ErrorState v-else-if="policy.error.value && !policy.data.value" :error="policy.error.value" surface="Policy" @retry="policy.refresh()" />
      <PolicySettings v-else-if="policy.data.value" :policy="policy.data.value" @saved="policy.refresh()" />
    </section>

    <section v-else>
      <LoadingState v-if="health.pending.value && !health.data.value" message="Checking system health…" />
      <ErrorState v-else-if="health.error.value && !health.data.value" :error="health.error.value" surface="System health" @retry="health.refresh()" />
      <SystemHealthPanel v-else-if="health.data.value" :health="health.data.value" />
    </section>
  </div>
</template>
