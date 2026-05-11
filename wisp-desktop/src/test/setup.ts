import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

// Mock window.wisp API
const mockWispAPI = {
  platform: vi.fn(() => 'darwin'),
  onMenuAction: vi.fn(() => vi.fn()),
  openFileDialog: vi.fn(() => Promise.resolve(null)),
  openInVSCode: vi.fn(() => Promise.resolve(true)),
  selectDirectory: vi.fn(() => Promise.resolve(null)),
  readFileAsDataUrl: vi.fn(() => Promise.resolve(null)),
  openThemeDialog: vi.fn(() => Promise.resolve(null)),
  listCustomThemes: vi.fn(() => []),
}

// @ts-expect-error - global window.wisp mock for tests
window.wisp = mockWispAPI

// Mock navigator.clipboard
Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn(() => Promise.resolve()),
  },
})

// Mock document.execCommand
document.execCommand = vi.fn(() => true)

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
}
Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
})

// Mock location.reload
Object.defineProperty(window, 'location', {
  value: { reload: vi.fn() },
  writable: true,
})
