<script setup lang="ts">
// Relative time with exact timestamp on hover (UI.md §49, §68).
const props = defineProps<{ at?: string | null; prefix?: string }>()
const now = useState('now', () => Date.now())
onMounted(() => { const t = setInterval(() => { now.value = Date.now() }, 30_000); onBeforeUnmount(() => clearInterval(t)) })
</script>

<template>
  <time v-if="props.at" class="meta" :datetime="props.at" :title="absoluteTime(props.at)">{{ props.prefix ?? '' }}{{ relativeTime(props.at, now) }}</time>
  <span v-else class="meta">{{ props.prefix ?? '' }}time not reported</span>
</template>
