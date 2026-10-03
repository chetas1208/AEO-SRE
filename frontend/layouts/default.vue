<script setup lang="ts">
const live = useLiveSystemStore()
const org = useOrganizationStore()
const navOpen = ref(false)
const route = useRoute()
watch(() => route.fullPath, () => { navOpen.value = false })

onMounted(() => {
  org.load()
  live.refresh()
  const t = setInterval(() => live.refresh(), 15_000)
  onBeforeUnmount(() => clearInterval(t))
})
</script>

<template>
  <div class="shell">
    <ClientOnly>
      <AmbientField />
    </ClientOnly>
    <AppNavRail :open="navOpen" @navigate="navOpen = false" />
    <div class="main">
      <AppTopBar @toggle-nav="navOpen = !navOpen" />
      <main class="content">
        <slot />
      </main>
    </div>
  </div>
</template>
