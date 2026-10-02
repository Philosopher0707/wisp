import React from 'react';
import type { CapabilityResponse } from '../hooks/useApi.js';

/**
 * The active principal's effective tool surface, for the topbar.
 *
 * This is NOT a variant of the subagent-level CapabilitySummary. The two
 * `null`-capabilities states mean opposite things:
 *
 *   - principal (this file):   `principal.unbounded: true` -> `capabilities is None`
 *                               means UNRESTRICTED (capabilities.py:107).
 *   - subagent (SubagentPanel): `capabilities: null` -> UNREPORTED.
 *
 * Reusing one component for both would render the single most authoritative
 * state as the single most restricted one, which is the exact failure this
 * whole surface exists to prevent.
 */
export const PrincipalCapabilityChip: React.FC<{
  /** `null` = the fetch failed. Unknown, not "no tools", not "unrestricted". */
  data: CapabilityResponse | null;
  loading?: boolean;
}> = ({ data, loading = false }) => {
  if (loading) {
    return <span className="wisp-cap-chip wisp-cap-chip--loading">checking...</span>;
  }

  if (data === null) {
    // Fail-closed and visually distinct: dashed, not a confident colour.
    // A missing read must never borrow the styling of a resolved answer.
    return (
      <span
        className="wisp-cap-chip wisp-cap-chip--unreported"
        title="Could not read the capability surface from the server. Authority is unknown."
      >
        authority unknown
      </span>
    );
  }

  // `principal.unbounded` is the server's own claim, read directly. It is deliberately
  // NOT recomputed from tool_count: a principal with capabilities=None
  // reports tool_count == registry_count, but that equality is a coincidence
  // of arithmetic, not a fact about authority.
  if (data.principal.unbounded) {
    return (
      <span
        className="wisp-cap-chip wisp-cap-chip--unbounded"
        title={`Unrestricted: every tool in the registry is permitted. ${data.authority_note}`}
      >
        unrestricted · {data.registry_count} tools
      </span>
    );
  }

  return (
    <span
      className="wisp-cap-chip wisp-cap-chip--bounded"
      title={data.authority_note}
    >
      {data.tool_count} / {data.registry_count} tools
    </span>
  );
};

export default PrincipalCapabilityChip;
