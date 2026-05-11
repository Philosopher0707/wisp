import { describe, it, expect } from 'vitest'
import { appReducer, createInitialState } from './types'
import type { AppState } from './types'

describe('createInitialState', () => {
  it('produces valid default state', () => {
    const state = createInitialState()
    expect(state.serverUrl).toBe('http://localhost:8000')
    expect(state.apiKey).toBe('')
    expect(state.connection).toBe('disconnected')
    expect(state.messages).toEqual([])
    expect(state.isStreaming).toBe(false)
    expect(state.vimMode).toBe(false)
    expect(state.theme).toBe('dark')
    expect(state.selectedModel).toBe('claude-sonnet-4-6')
    expect(state.availableModels).toEqual([])
    expect(state.inputValue).toBe('')
  })

  it('accepts overrides', () => {
    const state = createInitialState({
      serverUrl: 'http://custom:9000',
      apiKey: 'secret',
      selectedModel: 'gpt-4',
      pinnedSessionIds: ['s1'],
      systemPrompt: 'Be helpful',
      permissionMode: 'read_only',
    })
    expect(state.serverUrl).toBe('http://custom:9000')
    expect(state.apiKey).toBe('secret')
    expect(state.selectedModel).toBe('gpt-4')
    expect(state.pinnedSessionIds.has('s1')).toBe(true)
    expect(state.systemPrompt).toBe('Be helpful')
    expect(state.permissionMode).toBe('read_only')
  })
})

describe('appReducer', () => {
  const baseState: AppState = createInitialState()

  it('handles SET_INPUT', () => {
    const next = appReducer(baseState, { type: 'SET_INPUT', value: 'hello world' })
    expect(next.inputValue).toBe('hello world')
  })

  it('handles SET_MODELS', () => {
    const next = appReducer(baseState, {
      type: 'SET_MODELS',
      models: ['model-a', 'model-b'],
    })
    expect(next.availableModels).toEqual(['model-a', 'model-b'])
  })

  it('handles TOGGLE_VIM_MODE', () => {
    expect(baseState.vimMode).toBe(false)
    const next = appReducer(baseState, { type: 'TOGGLE_VIM_MODE' })
    expect(next.vimMode).toBe(true)
    const next2 = appReducer(next, { type: 'TOGGLE_VIM_MODE' })
    expect(next2.vimMode).toBe(false)
  })

  it('does not crash on unknown action types (default case)', () => {
    // @ts-expect-error unknown action type
    const next = appReducer(baseState, { type: 'UNKNOWN_ACTION' })
    expect(next).toBe(baseState)
    expect(next).toEqual(baseState)
  })

  it('preserves immutability for unknown actions', () => {
    // @ts-expect-error unknown action type
    const next = appReducer(baseState, { type: 'NON_EXISTENT' })
    expect(Object.is(next, baseState)).toBe(true)
  })
})
