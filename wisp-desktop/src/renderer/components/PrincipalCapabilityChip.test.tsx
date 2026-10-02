import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PrincipalCapabilityChip } from './PrincipalCapabilityChip'
import type { CapabilityResponse } from '../hooks/useApi.js'

const NOTE = 'Visibility only.'

function caps(over: Partial<CapabilityResponse> = {}): CapabilityResponse {
  return {
    principal: {
      id: 'p-1', kind: 'local', os_user: 'philosopher',
      workspace: '/ws', profile: 'default',
      org_id: null, parent_principal_id: null, unbounded: false,
    },
    tools: [],
    tool_count: 41,
    registry_count: 52,
    authority_note: NOTE,
    ...over,
  }
}

const cls = () => screen.getByText(/./).className

describe('PrincipalCapabilityChip', () => {
  // The whole point of the separate component. At PRINCIPAL level, a null
  // capability set means UNRESTRICTED — the inverse of the subagent meaning.
  // If this ever rendered like the subagent "unknown" state, the single most
  // authoritative principal would be displayed as the least known one.
  it('renders an unbounded principal as unrestricted, never as unknown', () => {
    render(<PrincipalCapabilityChip data={caps({ principal: { ...caps().principal, unbounded: true }, tool_count: 52 })} />)
    expect(screen.getByText(/unrestricted/)).toBeInTheDocument()
    expect(screen.queryByText(/authority unknown/)).not.toBeInTheDocument()
    expect(cls()).toContain('wisp-cap-chip--unbounded')
  })

  it('renders a bounded principal as a count against the registry', () => {
    render(<PrincipalCapabilityChip data={caps()} />)
    expect(screen.getByText('41 / 52 tools')).toBeInTheDocument()
    expect(cls()).toContain('wisp-cap-chip--bounded')
  })

  // A failed read must never borrow the styling of a resolved answer, and
  // must never be coerced into "0 tools" (which would read as max restriction).
  it('renders a failed read as unknown, not as zero tools', () => {
    render(<PrincipalCapabilityChip data={null} />)
    expect(screen.getByText('authority unknown')).toBeInTheDocument()
    expect(screen.queryByText(/0 tools/)).not.toBeInTheDocument()
    expect(cls()).toContain('wisp-cap-chip--unreported')
  })

  // Equal counts do NOT imply unbounded authority. The boolean is the only
  // source; inferring from arithmetic would be exactly the shortcut that
  // misreports a narrowed principal.
  it('does not infer unbounded from tool_count == registry_count', () => {
    render(<PrincipalCapabilityChip data={caps({ principal: { ...caps().principal, unbounded: false }, tool_count: 52 })} />)
    expect(screen.queryByText(/unrestricted/)).not.toBeInTheDocument()
    expect(screen.getByText('52 / 52 tools')).toBeInTheDocument()
  })

  it('shows a loading state before the first read resolves', () => {
    render(<PrincipalCapabilityChip data={null} loading />)
    expect(screen.getByText('checking...')).toBeInTheDocument()
    expect(screen.queryByText('authority unknown')).not.toBeInTheDocument()
  })
})
