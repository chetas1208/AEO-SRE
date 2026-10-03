import { describe, expect, it } from 'vitest'
import { assertProductionApiBase, isLoopbackApiBase, normalizeApiBase } from '~/utils/apiBase'

describe('apiBase production invariants', () => {
  it('rejects loopback in production', () => {
    expect(() => assertProductionApiBase('http://localhost:8000', { production: true })).toThrow(/loopback/i)
    expect(isLoopbackApiBase('http://127.0.0.1:8000')).toBe(true)
    expect(isLoopbackApiBase('https://api.example.com')).toBe(false)
  })

  it('requires HTTPS in production', () => {
    expect(() => assertProductionApiBase('http://api.example.com', { production: true })).toThrow(/HTTPS/)
    expect(() => assertProductionApiBase('https://somehow-air-animals-connectors.trycloudflare.com', { production: true })).not.toThrow()
    expect(() => assertProductionApiBase('', { production: true, sameOrigin: true })).not.toThrow()
  })

  it('normalizes trailing slashes', () => {
    expect(normalizeApiBase('https://api.test/')).toBe('https://api.test')
  })
})
