import type { Capability } from '~/types'

export const SYSTEM_COMPONENTS: Array<{ label: string; match: RegExp }> = [
  { label: 'API', match: /^api$|^app$/i }, { label: 'Database', match: /data ?base|^db$|postgres/i }, { label: 'Redis', match: /redis|queue/i },
  { label: 'Workers', match: /worker/i }, { label: 'Profound connector', match: /profound/i }, { label: 'Crawler', match: /crawl|web/i },
  { label: 'Evidence model', match: /evidence|ranker/i }, { label: 'Policy model', match: /policy|bandit/i }
]

export function findCapability(caps: Capability[], match: RegExp): Capability | null {
  return caps.find((c) => match.test(c.name)) ?? null
}
