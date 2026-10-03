import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { existsSync } from 'node:fs'
import { config as loadDotenv } from 'dotenv'

// Single root .env (../.env). No frontend-local env file. Only the public API base URL is exposed to the client.
const here = dirname(fileURLToPath(import.meta.url))
const rootEnv = resolve(here, '..', '.env')
if (existsSync(rootEnv)) loadDotenv({ path: rootEnv, quiet: true })

const apiFromEnv = (process.env.NUXT_PUBLIC_API_BASE_URL || '').trim()
/** Override via Vercel env; fallback only for Vercel builds when env missing (see DEPLOYMENT.md). */
const VERCEL_API_FALLBACK = 'https://somehow-air-animals-connectors.trycloudflare.com'
const resolvedApiBase =
  apiFromEnv
  || (process.env.VERCEL === '1' ? VERCEL_API_FALLBACK : '')
  || 'http://localhost:8000'
if (process.env.VERCEL_ENV === 'production' && !apiFromEnv) {
  console.warn(
    '[AgentMatch] NUXT_PUBLIC_API_BASE_URL unset on Vercel; using tunnel fallback. Set the env var and redeploy for a stable hostname.'
  )
}

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  ssr: false,
  devtools: { enabled: false },
  modules: ['@pinia/nuxt', '@nuxtjs/tailwindcss'],
  css: ['~/assets/css/main.css'],
  components: [{ path: '~/components', pathPrefix: false }],
  runtimeConfig: {
    public: {
      /** Canonical public API origin (Cloudflare tunnel hostname). Dev-only fallback to localhost. */
      apiBaseUrl: resolvedApiBase
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
