import type { PolicyInfo, SystemHealth } from '~/types'

/** Page-level health (Settings → System). The global live pill polls the same endpoints through useLiveSystemStore. */
export function useSystemHealth() {
  const res = useApiData<[unknown, unknown], SystemHealth>(
    'system-health',
    async () => {
      const [h, c] = await Promise.allSettled([apiFetch('/api/health'), apiFetch('/api/system/capabilities')])
      if (h.status === 'rejected' && c.status === 'rejected') throw h.reason
      return [h.status === 'fulfilled' ? h.value : null, c.status === 'fulfilled' ? c.value : null] as [unknown, unknown]
    },
    ([h, c]) => normHealth(h, c)
  )
  useAutoRefresh(() => res.refresh(), 30_000)
  return res
}

export function usePolicy() {
  return useApiData<unknown, PolicyInfo>('policy', () => apiFetch('/api/policy'), normPolicy)
}

export function useSettings() {
  return useApiData<unknown, Record<string, any>>('settings', () => apiFetch('/api/settings'), (r) => (r ?? {}) as Record<string, any>)
}
