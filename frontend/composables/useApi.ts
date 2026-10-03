import type { ApiErrorInfo } from '~/types'
import { normalizeApiBase } from '~/utils/apiBase'
import { guardErrorMessage } from '~/utils/guard'

export function useApiBase(): string {
  return normalizeApiBase(String(useRuntimeConfig().public.apiBaseUrl || ''))
}

/** Error codes the UI branches on (see docs/UI_API_CONTRACT.md). */
export const ERR = {
  NOT_VERIFIABLE_YET: 'EXPERIMENT_NOT_VERIFIABLE_YET',
  ACTION_NOT_ELIGIBLE: 'ACTION_NOT_ELIGIBLE',
  INVALID_STATE_TRANSITION: 'INVALID_STATE_TRANSITION'
} as const

function friendlyMessage(code: string | undefined, details: Record<string, unknown> | null | undefined): string | undefined {
  if (code === ERR.NOT_VERIFIABLE_YET) {
    const at = typeof details?.eligible_at === 'string' ? details.eligible_at : typeof details?.eligibleAt === 'string' ? details.eligibleAt : null
    return at ? `Not eligible yet: the verification window opens ${absoluteTime(at)} (eligible at ${at}).` : 'Not eligible yet: the verification window has not opened.'
  }
  return guardErrorMessage(code)
}

export function toApiError(e: unknown): ApiErrorInfo {
  const err = e as { statusCode?: number; status?: number; data?: any; message?: string }
  const status = err?.statusCode ?? err?.status
  // Backend error envelope: {"error": {"code", "type", "message", "details", "request_id"}}. Read code first, then legacy type.
  const body = err?.data?.error ?? err?.data
  const type: string | undefined = typeof body?.type === 'string' ? body.type : undefined
  const code: string | undefined = typeof body?.code === 'string' ? body.code : type
  const details: Record<string, unknown> | null = body?.details && typeof body.details === 'object' ? body.details : null
  const requestId: string | undefined = typeof body?.request_id === 'string' ? body.request_id : undefined
  const detail: string | undefined =
    typeof body?.message === 'string' ? body.message : typeof body?.detail === 'string' ? body.detail : undefined
  const extra = { code, requestId, details }
  // No HTTP status => the request never completed (network down / API unreachable). 502/503/504 or type=unavailable => dependency unavailable.
  if (!status) return { kind: 'unavailable', message: 'API is unreachable.', detail: err?.message }
  if (type === 'unavailable' || status === 502 || status === 503 || status === 504) {
    return { kind: 'unavailable', status, message: `API reported the data source as unavailable (HTTP ${status}).`, detail, ...extra }
  }
  const friendly = friendlyMessage(code, details)
  return { kind: 'failed', status, message: friendly ?? `API request failed (HTTP ${status}).`, detail: friendly ? undefined : detail, ...extra }
}

/**
 * 409 on a REPEATED mutation (server says the change is already recorded: details.reason `already_*`, or a duplicate reward)
 * is not a failure: callers refetch and show the server's state (UI.md section 55). Genuine refusals stay errors:
 * ACTION_NOT_ELIGIBLE, EXPERIMENT_NOT_VERIFIABLE_YET and illegal transitions (e.g. approve after reject).
 */
export function isAlreadyApplied(e: unknown): boolean {
  const x = e as ApiErrorInfo
  if (x?.status !== 409) return false
  const reason = x.details?.reason
  return (typeof reason === 'string' && reason.startsWith('already')) || x.code === 'DUPLICATE_REWARD'
}

/** Typed JSON call against the backend. Keys are camelized; failures are thrown as ApiErrorInfo. */
export async function apiFetch<T = any>(
  path: string,
  opts: { method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'; query?: Record<string, unknown>; body?: unknown; signal?: AbortSignal; headers?: Record<string, string> } = {}
): Promise<T> {
  const base = useApiBase()
  const query: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null && v !== '') query[k] = v
  try {
    const raw = await $fetch<unknown>(`${base}${path}`, {
      method: opts.method ?? 'GET',
      query,
      body: opts.body as any,
      signal: opts.signal,
      credentials: 'include',
      headers: { Accept: 'application/json', ...(opts.headers ?? {}) }
    })
    return camelize<T>(raw)
  } catch (e) {
    throw toApiError(e)
  }
}

/** useAsyncData wrapper: normalizes the payload and exposes a typed ApiErrorInfo. */
export function useApiData<Raw, T>(
  key: MaybeRefOrGetter<string>,
  load: () => Promise<Raw>,
  map: (raw: Raw) => T,
  opts: { watch?: any[]; enabled?: MaybeRefOrGetter<boolean> } = {}
) {
  const res = useAsyncData<T, ApiErrorInfo>(
    key as any,
    async () => {
      if (opts.enabled !== undefined && !toValue(opts.enabled)) return null as unknown as T
      return map(await load())
    },
    { watch: opts.watch as any, server: false, default: () => null as unknown as T }
  )
  return { ...res, error: res.error as Ref<ApiErrorInfo | undefined | null> }
}

/** Periodically refresh while mounted (client only). */
export function useAutoRefresh(refresh: () => unknown, ms: number) {
  let timer: ReturnType<typeof setInterval> | undefined
  onMounted(() => { timer = setInterval(() => { if (!document.hidden) refresh() }, ms) })
  onBeforeUnmount(() => { if (timer) clearInterval(timer) })
}
