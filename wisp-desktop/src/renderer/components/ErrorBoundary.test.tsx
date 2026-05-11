import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import React from 'react'
import { ErrorBoundary } from './ErrorBoundary'

// Component that throws
const Thrower = () => {
  throw new Error('Test error from Thrower')
}

describe('ErrorBoundary', () => {
  const originalConsoleError = console.error

  beforeEach(() => {
    console.error = vi.fn()
  })

  afterEach(() => {
    console.error = originalConsoleError
  })

  it('renders children when there is no error', () => {
    render(
      <ErrorBoundary>
        <div data-testid="content">Hello World</div>
      </ErrorBoundary>,
    )
    expect(screen.getByTestId('content')).toBeInTheDocument()
    expect(screen.getByText('Hello World')).toBeInTheDocument()
  })

  it('catches errors and renders fallback UI', () => {
    render(
      <ErrorBoundary>
        <Thrower />
      </ErrorBoundary>,
    )

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText(/Test error from Thrower/)).toBeInTheDocument()

    const reloadBtn = screen.getByRole('button', { name: /Reload App/i })
    const copyBtn = screen.getByRole('button', { name: /Copy Error/i })
    const retryBtn = screen.getByRole('button', { name: /Try Again/i })

    expect(reloadBtn).toBeInTheDocument()
    expect(copyBtn).toBeInTheDocument()
    expect(retryBtn).toBeInTheDocument()
  })

  it('calls window.location.reload when Reload App is clicked', () => {
    render(
      <ErrorBoundary>
        <Thrower />
      </ErrorBoundary>,
    )

    const reloadBtn = screen.getByRole('button', { name: /Reload App/i })
    fireEvent.click(reloadBtn)
    expect(window.location.reload).toHaveBeenCalled()
  })

  it('copies error to clipboard when Copy Error is clicked', () => {
    render(
      <ErrorBoundary>
        <Thrower />
      </ErrorBoundary>,
    )

    const copyBtn = screen.getByRole('button', { name: /Copy Error/i })
    fireEvent.click(copyBtn)

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining('Test error from Thrower'),
    )
  })
})
