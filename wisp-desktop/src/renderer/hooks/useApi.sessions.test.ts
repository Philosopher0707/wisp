import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useApi } from './useApi'

/** Session and workspace calls against the response shapes the real server sends (see wisp/server/routes/sessions.py, workspace.py). */

const BASE = 'http://127.0.0.1:8765'

function res(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this
    },
  }
}

describe('session and workspace calls', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  const api = () => renderHook(() => useApi(BASE, 'k')).result.current

  it('deleteSession is true for the server\'s {"deleted": true} (it only checked `ok`, so deletes always looked failed)', async () => {
    fetchMock.mockResolvedValue(res(200, { deleted: true }))
    expect(await api().deleteSession('s1')).toBe(true)
  })

  it('deleteSession is false for a refusal and for a network error', async () => {
    fetchMock.mockResolvedValueOnce(res(409, { detail: 'lives in the global store' }))
    expect(await api().deleteSession('s1')).toBe(false)
    fetchMock.mockRejectedValueOnce(new Error('offline'))
    expect(await api().deleteSession('s1')).toBe(false)
  })

  it('renameSession is true for {"ok": true}', async () => {
    fetchMock.mockResolvedValue(res(200, { ok: true, session_id: 's1' }))
    expect(await api().renameSession('s1', 'new title')).toBe(true)
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body as string)).toEqual({ title: 'new title' })
  })

  it('importSession posts to the import route and reports failure', async () => {
    fetchMock.mockResolvedValueOnce(res(200, { imported: true, source: 'global' }))
    expect(await api().importSession('a b')).toBe(true)
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/sessions/a%20b/import`)
    fetchMock.mockResolvedValueOnce(res(404, { detail: 'Session not found' }))
    expect(await api().importSession('x')).toBe(false)
  })

  it('setWorkspace returns the applied path', async () => {
    fetchMock.mockResolvedValue(res(200, { path: '/Users/me/proj' }))
    expect(await api().setWorkspace('/Users/me/proj')).toBe('/Users/me/proj')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ path: '/Users/me/proj' })
  })

  it('setWorkspace throws the server\'s reason so the UI can show it', async () => {
    fetchMock.mockResolvedValue(res(400, { detail: 'Workspace path must be within allowed roots: /Users/me' }))
    await expect(api().setWorkspace('/etc')).rejects.toThrow(/allowed roots/)
  })
})
