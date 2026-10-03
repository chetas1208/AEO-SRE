const LOOPBACK = /^(https?:\/\/)?(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?/i

export function normalizeApiBase(raw: string): string {
  return String(raw || '').replace(/\/+$/, '')
}

/** Production builds must not ship a loopback API base (browser would hit the visitor's machine). */
export function assertProductionApiBase(base: string, env: { production?: boolean; vercel?: boolean }): void {
  const prod = Boolean(env.production || env.vercel)
  if (!prod) return
  const b = normalizeApiBase(base)
  if (!b) {
    throw new Error('NUXT_PUBLIC_API_BASE_URL is required in production (Vercel env → redeploy).')
  }
  if (LOOPBACK.test(b)) {
    throw new Error(`Production API base must not be loopback: ${b}`)
  }
  if (!b.startsWith('https://')) {
    throw new Error(`Production API base must be HTTPS, got: ${b}`)
  }
}

export function isLoopbackApiBase(base: string): boolean {
  return LOOPBACK.test(normalizeApiBase(base))
}

/** True when the SPA is on a public host but API base still points at loopback (stale/missing Vercel build env). */
export function isBrowserApiMisconfigured(): boolean {
  if (typeof window === 'undefined') return false
  const host = window.location.hostname
  if (host === 'localhost' || host === '127.0.0.1') return false
  return isLoopbackApiBase(normalizeApiBase(String(useRuntimeConfig().public.apiBaseUrl || '')))
}
