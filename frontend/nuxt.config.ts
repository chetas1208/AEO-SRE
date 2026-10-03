import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { existsSync } from 'node:fs'
import { config as loadDotenv } from 'dotenv'

// Single root .env (../.env). No frontend-local env file. Only the public API base URL is exposed to the client.
const here = dirname(fileURLToPath(import.meta.url))
const rootEnv = resolve(here, '..', '.env')
if (existsSync(rootEnv)) loadDotenv({ path: rootEnv, quiet: true })

const apiFromEnv = (process.env.NUXT_PUBLIC_API_BASE_URL || '').trim()
const backendProxy = (process.env.NUXT_BACKEND_PROXY_URL || '').trim().replace(/\/+$/, '')
const apiSameOrigin =
  process.env.NUXT_PUBLIC_API_SAME_ORIGIN === '1'
  || (process.env.VERCEL === '1' && backendProxy.length > 0)
const isLoopback = (u: string) => /^(https?:\/\/)?(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?/i.test(u)
/** Fallback to active Cloudflare tunnel when not using same-origin /api proxy. */
const VERCEL_API_FALLBACK = 'https://somehow-air-animals-connectors.trycloudflare.com'
const resolvedApiBase = apiSameOrigin
  ? ''
  : (apiFromEnv && !isLoopback(apiFromEnv))
    ? apiFromEnv
    : (process.env.NODE_ENV === 'development' && !process.env.VERCEL ? 'http://localhost:8000' : VERCEL_API_FALLBACK)

const routeRules: Record<string, { proxy: string }> = {}
if (backendProxy) {
  routeRules['/api/**'] = { proxy: `${backendProxy}/api/**` }
}

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  ssr: false,
  devtools: { enabled: false },
  modules: ['@pinia/nuxt', '@nuxtjs/tailwindcss'],
  css: ['~/assets/css/main.css'],
  components: [{ path: '~/components', pathPrefix: false }],
  routeRules,
  runtimeConfig: {
    public: {
      /** Empty when Vercel proxies /api to the backend (same-origin; no CORS). */
      apiBaseUrl: resolvedApiBase,
      apiSameOrigin: apiSameOrigin
    }
  },
  app: {
    head: {
      title: 'AEO SRE',
      htmlAttrs: { lang: 'en' },
      meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1' }]
    }
  },
  typescript: { strict: true, typeCheck: false },
  vite: { server: { fs: { allow: [here, resolve(here, '..')] }, watch: { ignored: ['**/test-artifacts/**', '**/e2e/**'] } } }
})
