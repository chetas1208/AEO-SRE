import { assertProductionApiBase, isLoopbackApiBase, normalizeApiBase } from '~/utils/apiBase'

export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig()
  const base = normalizeApiBase(String(config.public.apiBaseUrl || ''))
  assertProductionApiBase(base, {
    production: import.meta.env.PROD,
    vercel: process.env.VERCEL === '1',
    sameOrigin: Boolean(config.public.apiSameOrigin)
  })
  if (import.meta.env.PROD && isLoopbackApiBase(base)) {
    console.error('[Profound Lift] Misconfigured API base in production:', base)
  }
})
