// Keys under which payloads are free-form dictionaries whose own keys must be preserved verbatim.
const OPAQUE_KEYS = new Set([
  'metadata', 'context', 'raw', 'components', 'weights', 'scores', 'contextVector', 'meta', 'data',
  'beforeMetrics', 'afterMetrics', 'evidenceSnapshot', 'counts', 'countsBySeverity', 'countsByStatus', 'countsByState',
  'countsByType', 'proposedChange', 'approvedChange', 'modifiedChange', 'allowedActions', 'payload', 'result', 'state', 'priors',
  'details'
])

export function toCamel(key: string): string {
  return key.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase())
}

export function camelize<T = unknown>(value: unknown, opaque = false): T {
  if (Array.isArray(value)) return value.map((v) => camelize(v, opaque)) as T
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      const nk = opaque ? k : toCamel(k)
      // opaque dictionaries keep their keys; nested plain objects inside still get arrays walked
      out[nk] = camelize(v, opaque || OPAQUE_KEYS.has(nk))
    }
    return out as T
  }
  return value as T
}
