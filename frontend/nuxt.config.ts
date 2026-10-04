import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { existsSync } from 'node:fs'
import { config as loadDotenv } from 'dotenv'
import { isLoopbackApiUrl, resolvePublicApiOrigin } from './utils/resolvePublicApi'

// Single root .env (../.env). Public backend = Cloudflare tunnel (API_PUBLIC_URL). Browser uses same-origin /api when proxied.
const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(here, '..')
const rootEnv = resolve(repoRoot, '.env')
if (existsSync(rootEnv)) loadDotenv({ path: rootEnv, quiet: true })

const publicApiOrigin = resolvePublicApiOrigin(repoRoot)
const explicitPublic = (process.env.NUXT_PUBLIC_API_BASE_URL || '').trim()
const sameOriginPref = process.env.NUXT_PUBLIC_API_SAME_ORIGIN
const apiSameOrigin = publicApiOrigin.length > 0 && (
  sameOriginPref === '1' || (process.env.VERCEL === '1' && sameOriginPref !== '0')
)
const resolvedApiBase = apiSameOrigin
  ? ''
  : (explicitPublic && !isLoopbackApiUrl(explicitPublic))
    ? explicitPublic.replace(/\/+$/, '')
    : (publicApiOrigin || (process.env.NODE_ENV === 'development' && !process.env.VERCEL ? 'http://localhost:8000' : ''))

const routeRules: Record<string, { proxy: string }> = {}
if (publicApiOrigin) {
  routeRules['/api/**'] = { proxy: `${publicApiOrigin}/api/**` }
}

const viteProxy = publicApiOrigin && !process.env.VERCEL
  ? { '/api': { target: publicApiOrigin, changeOrigin: true, secure: true } }
  : undefined

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
      apiSameOrigin: apiSameOrigin,
      /** Cloudflare tunnel (canonical backend URL); for display/diagnostics only. */
      apiPublicOrigin: publicApiOrigin || resolvedApiBase
    }
  },
  app: {
    head: {
      title: 'Profound Lift',
      titleTemplate: '%s · Profound Lift',
      htmlAttrs: { lang: 'en' },
      meta: [
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'application-name', content: 'Profound Lift' },
      ],
    }
  },
  typescript: { strict: true, typeCheck: false },
  vite: {
    server: {
      fs: { allow: [here, repoRoot] },
      watch: { ignored: ['**/test-artifacts/**', '**/e2e/**'] },
      ...(viteProxy ? { proxy: viteProxy } : {})
    }
  }
})
