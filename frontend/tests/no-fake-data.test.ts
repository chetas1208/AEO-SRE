// Hard rule: no demo mode / hardcoded incidents or metrics in shipped frontend source.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = join(__dirname, '..')
const SKIP = new Set(['node_modules', '.nuxt', '.output', 'tests', 'dist', '.git'])
const EXT = /\.(vue|ts|js|mjs|css|html)$/

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (SKIP.has(name)) continue
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (EXT.test(name) && !name.endsWith('.generated.ts')) out.push(p)
  }
  return out
}
const files = walk(ROOT).map((p) => ({ p: p.slice(ROOT.length + 1), text: readFileSync(p, 'utf8') }))

const lines = (re: RegExp) =>
  files.flatMap(({ p, text }) => text.split('\n').flatMap((l, i) => (re.test(l) ? [`${p}:${i + 1}: ${l.trim().slice(0, 100)}`] : [])))

describe('no fake data in shipped frontend code', () => {
  it('scans real source', () => expect(files.length).toBeGreaterThan(20))
  it('has no DEMO_MODE / demo flags', () => expect(lines(/DEMO[_-]?MODE|demoMode|isDemo\b|IS_DEMO/i)).toEqual([]))
  it('has no /demo route', () => {
    expect(lines(/["'`]\/demo(["'`/?]|$)/)).toEqual([])
    expect(files.filter((f) => /pages\/.*demo/i.test(f.p))).toEqual([])
  })
  it('has no hardcoded mockup incident text or invented money', () =>
    expect(lines(/Enterprise SSO visibility|Visibility Loss -24pp|\$2\.7M|lorem ipsum/i)).toEqual([]))
  it('never claims success before the backend does (UI.md section 55)', () =>
    expect(lines(/Successfully deployed|successfully executed/i)).toEqual([]))
  it('does not compute rewards on the client', () =>
    expect(files.filter((f) => /\/(composables|components|stores|utils|pages)\//.test('/' + f.p) && /tanh\(|reward\s*=\s*[^=]*\*/.test(f.text)).map((f) => f.p)).toEqual([]))
  it('keeps default flows executor-agnostic (no GitHub-centric copy; GitHub only as an optional executor label)', () => {
    expect(lines(/Approve & Execute|Approve and Execute/)).toEqual([])
    expect(lines(/GitHub →|github →|opens? a PR|pull request|create(s|d)? a branch/i)).toEqual([])
  })
})
