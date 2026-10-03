import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const LOOPBACK = /^(https?:\/\/)?(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?/i

/** Canonical public backend origin (Cloudflare tunnel). Never loopback. */
export function resolvePublicApiOrigin(repoRoot: string): string {
  const fromEnv = (process.env.API_PUBLIC_URL || process.env.NUXT_BACKEND_PROXY_URL || process.env.NUXT_PUBLIC_API_BASE_URL || '').trim()
  if (fromEnv && !LOOPBACK.test(fromEnv)) {
    return fromEnv.replace(/\/+$/, '')
  }
  const tunnelFile = resolve(repoRoot, 'logs/cloudflare/tunnel_url.txt')
  if (existsSync(tunnelFile)) {
    const t = readFileSync(tunnelFile, 'utf8').trim()
    if (t.startsWith('https://')) return t.replace(/\/+$/, '')
  }
  return ''
}

export function isLoopbackApiUrl(url: string): boolean {
  return LOOPBACK.test(String(url || '').trim())
}
