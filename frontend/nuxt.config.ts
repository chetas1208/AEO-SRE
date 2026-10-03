import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { existsSync } from 'node:fs'
import { config as loadDotenv } from 'dotenv'

// Single root .env (../.env). No frontend-local env file. Only the public API base URL is exposed to the client.
const here = dirname(fileURLToPath(import.meta.url))
const rootEnv = resolve(here, '..', '.env')
if (existsSync(rootEnv)) loadDotenv({ path: rootEnv, quiet: true })

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  ssr: false,
  devtools: { enabled: false },
  modules: ['@pinia/nuxt', '@nuxtjs/tailwindcss'],
  css: ['~/assets/css/main.css'],
  components: [{ path: '~/components', pathPrefix: false }],
  runtimeConfig: {
    public: {
      // overridable at runtime via NUXT_PUBLIC_API_BASE_URL
      apiBaseUrl: process.env.NUXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
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
