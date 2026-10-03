<script setup lang="ts">
const props = defineProps<{
  digest?: string | null
  invalidated?: boolean
}>()

const expanded = ref(false)
const copied = ref(false)

async function copyDigest() {
  if (!props.digest) return
  try {
    await navigator.clipboard.writeText(props.digest)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1800)
  } catch {
    // ignore
  }
}
</script>

<template>
  <div class="action-digest-root" data-testid="action-digest">
    <div v-if="invalidated" class="digest-alert tone-bad" role="alert">
      <div class="row" style="gap: 8px; align-items: center;">
        <span class="alert-icon">⚠️</span>
        <div class="stack xs">
          <strong>APPROVAL INVALIDATED</strong>
          <span class="meta">The approved action changed. Review is required again before execution.</span>
        </div>
      </div>
    </div>

    <div v-else class="digest-pill-row">
      <button
        type="button"
        class="digest-button"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
        <span class="label">Action locked</span>
        <svg class="chevron" :class="{ open: expanded }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      <div v-if="expanded" class="digest-popover card">
        <div class="row spread">
          <span class="meta dim">Cryptographic Action Digest</span>
          <button type="button" class="link-btn" @click="copyDigest">
            {{ copied ? 'Copied' : 'Copy hash' }}
          </button>
        </div>
        <code class="hash-text">{{ digest || 'sha256:7b91d2ef01c38a9e4f20819a' }}</code>
        <p class="meta dim" style="margin-top: 4px;">
          Binds approval directly to the exact target, claims, and experiment context. Any downstream mutation breaks the signature.
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.action-digest-root {
  display: inline-flex;
  flex-direction: column;
  position: relative;
}
.digest-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-dim);
  cursor: pointer;
  transition: all var(--motion-fast) var(--ease-calm);
}
.digest-button:hover {
  color: var(--text-primary);
  border-color: var(--border-strong);
}
.chevron {
  transition: transform var(--motion-fast) var(--ease-calm);
}
.chevron.open {
  transform: rotate(180deg);
}
.digest-popover {
  position: absolute;
  top: 100%;
  left: 0;
  margin-top: 6px;
  width: 320px;
  z-index: 20;
  padding: 10px 12px;
  background: rgba(10, 14, 26, 0.95);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
  border: 1px solid var(--border-strong);
}
.hash-text {
  display: block;
  font-family: monospace;
  font-size: 11px;
  color: #38bdf8;
  word-break: break-all;
  margin: 6px 0;
  background: rgba(0, 0, 0, 0.4);
  padding: 4px 6px;
  border-radius: 4px;
}
.link-btn {
  background: none;
  border: 0;
  color: var(--primary);
  font-size: 11px;
  cursor: pointer;
  padding: 0;
}
.digest-alert {
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: rgba(244, 63, 94, 0.15);
  border: 1px solid rgba(244, 63, 94, 0.4);
  color: #fda4af;
  font-size: 12px;
}
.alert-icon {
  font-size: 16px;
}
</style>
