import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useApi } from './useApi'

/**
 * Authentication-header guard for the API client.
 *
 * Every request the client makes must carry the API key in the `Authorization`
 * header. This pins a real defect: `getCheckpointDiff` builds its own `fetch`
 * call — it reads a plain-text diff, so it cannot go through `apiFetch`, which
 * parses JSON — and it called the header builder with **no argument**. The
 * builder returns `{}` for a falsy key, so the checkpoint-diff request went out
 * unauthenticated while every other request was authenticated. `authParams` is
 * the empty string (the key is deliberately never sent as a query param, where
 * it would leak to logs), so there was no fallback either.
 *
 * The second half of the guard is the canonicalization: there must be exactly
 * one way to build the header, so a future raw-`fetch` call cannot drift from
 * it again.
 */

const API_KEY = 'secret-key-123'
const BASE_URL = 'http://127.0.0.1:8765'

function okJson(body: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this
    },
  }
}

function okText(body: string) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => {
      throw new Error('not JSON')
    },
    text: async () => body,
    clone() {
      return this
    },
  }
}

describe('useApi — Authorization header', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  /** Headers of the Nth fetch call, as a plain object. */
  const headersOf = (call: number): Record<string, string> => {
    const init = fetchMock.mock.calls[call]?.[1] as RequestInit | undefined
    return (init?.headers ?? {}) as Record<string, string>
  }

  // ── The defect: the raw-fetch path ──────────────────────────────────────

  it('sends the key on the checkpoint-diff request (raw fetch path)', async () => {
    fetchMock.mockResolvedValue(okText('--- a\n+++ b\n'))
    const { result } = renderHook(() => useApi(BASE_URL, API_KEY))

    const diff = await result.current.getCheckpointDiff('cp-1')

    // Presence assertions: the request must actually have happened, or the
    // header assertion below would pass vacuously against a never-made call.
    expect(diff).toBe('--- a\n+++ b\n')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(headersOf(0).Authorization).toBe(`Bearer ${API_KEY}`)
  })

  // ── The canonical path, pinned so it cannot drift ───────────────────────

  it('sends the key on a plain GET through apiFetch', async () => {
    fetchMock.mockResolvedValue(okJson({ sessions: [] }))
    const { result } = renderHook(() => useApi(BASE_URL, API_KEY))

    await result.current.fetchSessions()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(headersOf(0).Authorization).toBe(`Bearer ${API_KEY}`)
  })

  it('sends the key on a POST with a body through apiFetch', async () => {
    fetchMock.mockResolvedValue(okJson({ ok: true }))
    const { result } = renderHook(() => useApi(BASE_URL, API_KEY))

    await result.current.installPlugin('/tmp/some-plugin')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(headersOf(0).Authorization).toBe(`Bearer ${API_KEY}`)
    expect(headersOf(0)['Content-Type']).toBe('application/json')
  })

  // ── Anti-vacuous: an empty key must NOT produce a header ────────────────

  it('omits the header entirely when no key is configured', async () => {
    fetchMock.mockResolvedValue(okJson({ sessions: [] }))
    const { result } = renderHook(() => useApi(BASE_URL, ''))

    await result.current.fetchSessions()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(headersOf(0).Authorization).toBeUndefined()
  })

  it('omits the header on the raw-fetch path too when no key is configured', async () => {
    fetchMock.mockResolvedValue(okText(''))
    const { result } = renderHook(() => useApi(BASE_URL, ''))

    await result.current.getCheckpointDiff('cp-1')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(headersOf(0).Authorization).toBeUndefined()
  })
})
