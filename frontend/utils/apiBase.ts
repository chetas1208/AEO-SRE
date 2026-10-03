const LOOPBACK = /^(https?:\/\/)?(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?/i

export function normalizeApiBase(raw: string): string {
  return String(raw || '').replace(/\/+$/, '')
}

/** Production builds must not ship a loopback API base (browser would hit the visitor's machine). */
export function assertProductionApiBase(
  base: string,
  env: { production?: boolean; vercel?: boolean; sameOrigin?: boolean }
): void {
  const prod = Boolean(env.production || env.vercel)
  if (!prod) return
  if (env.sameOrigin) return
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

export const DEFAULT_PUBLIC_TUNNEL = 'https://somehow-air-animals-connectors.trycloudflare.com'

/** True only when the SPA is on a public host and has no reachable API (neither configured nor fallback tunnel). */
export function isBrowserApiMisconfigured(): boolean {
  if (typeof window === 'undefined') return false
  if (typeof useRuntimeConfig !== 'function') return false
  const config = useRuntimeConfig()
  if (config.public.apiSameOrigin) return false
  const host = window.location.hostname
  if (host === 'localhost' || host === '127.0.0.1') return false
  const raw = normalizeApiBase(String(config.public.apiBaseUrl || ''))
  if (raw && !isLoopbackApiBase(raw)) return false
  return !DEFAULT_PUBLIC_TUNNEL
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function isUuid(value: string | null | undefined): boolean {
  return typeof value === 'string' && UUID_RE.test(value.trim())
}
