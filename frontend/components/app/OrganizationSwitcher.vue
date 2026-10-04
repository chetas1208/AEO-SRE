<script setup lang="ts">
const org = useOrganizationStore()
const modalOpen = ref(false)
const adding = ref(false)
const domain = ref('')
const busy = ref(false)
const err = ref<string | null>(null)

const currentInitial = computed(() => {
  const name = org.current?.name || org.current?.domain || 'Acme Corp'
  return name.charAt(0).toUpperCase()
})

async function submit() {
  if (!domain.value.trim()) return
  busy.value = true
  err.value = null
  try {
    await org.add(domain.value.trim())
    domain.value = ''
    adding.value = false
    modalOpen.value = false
    await navigateTo('/incidents')
  } catch (e) {
    err.value = (e as { message?: string }).message ?? 'Could not add organization.'
  } finally {
    busy.value = false
  }
}

function onSelectOrg(id: string) {
  org.select(id)
  modalOpen.value = false
  navigateTo('/incidents')
}
</script>

<template>
  <div class="org-switcher-wrapper">
    <button class="org-button" type="button" @click="modalOpen = !modalOpen" aria-label="Switch organization">
      <div class="org-avatar">{{ currentInitial }}</div>
      <div class="org-info">
        <span class="org-name">{{ org.current?.name ?? 'No organization' }}</span>
        <span v-if="org.current?.domain" class="org-domain">{{ org.current.domain }}</span>
      </div>
      <svg class="org-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>

    <dialog :open="modalOpen" class="org-dialog">
      <div class="dlg">
        <div class="row spread">
          <h3>Organizations</h3>
          <button class="link" type="button" @click="modalOpen = false">Close</button>
        </div>

        <div v-if="org.orgs.length" class="org-list">
          <button
            v-for="o in org.orgs"
            :key="o.id"
            class="org-item"
            :class="{ active: o.id === org.current?.id }"
            type="button"
            @click="onSelectOrg(o.id)"
          >
            <div class="org-avatar sm">{{ o.name?.charAt(0) || 'O' }}</div>
            <div class="org-info">
              <span class="org-name">{{ o.name }}</span>
              <span class="org-domain">{{ o.domain }}</span>
            </div>
            <span v-if="o.id === org.current?.id" class="badge tone-good">Active</span>
          </button>
        </div>

        <div v-if="!adding" style="margin-top: 8px;">
          <button type="button" @click="adding = true">+ Add Organization</button>
        </div>

        <form v-else class="stack sm" @submit.prevent="submit" style="margin-top: 8px;">
          <label class="meta" for="org-domain-input">Company Domain</label>
          <input id="org-domain-input" v-model="domain" placeholder="example.com" autocomplete="off" required>
          <div class="row">
            <button class="primary" type="submit" :disabled="busy">{{ busy ? 'Adding…' : 'Add Organization' }}</button>
            <button type="button" @click="adding = false">Cancel</button>
          </div>
          <p v-if="err" class="meta tone-bad" role="alert">{{ err }}</p>
        </form>
      </div>
    </dialog>
  </div>
</template>

<style scoped>
.org-switcher-wrapper {
  position: relative;
}
.org-button {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 6px 8px;
  text-align: left;
  transition: all var(--motion-fast) var(--ease-calm);
}
.org-button:hover {
  background: var(--surface-hover);
  border-color: var(--border-subtle);
}
.org-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(135deg, #0284c7 0%, #38bdf8 100%);
  color: #ffffff;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 13px;
  flex-shrink: 0;
  box-shadow: 0 0 10px rgba(56, 189, 248, 0.35);
}
.org-avatar.sm {
  width: 24px;
  height: 24px;
  font-size: 11px;
}
.org-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}
.org-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.org-domain {
  font-size: 11px;
  color: var(--text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.org-chevron {
  color: var(--text-faint);
  flex-shrink: 0;
}

.org-dialog {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 100;
}
.org-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow-y: auto;
}
.org-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 12px;
  text-align: left;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-card);
}
.org-item.active {
  border-color: var(--primary);
  background: var(--primary-soft);
}
</style>
