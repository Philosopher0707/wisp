# BGP session down or flapping

## Symptoms
A neighbor's session-state is not ESTABLISHED (IDLE, CONNECT, ACTIVE, OPENSENT, OPENCONFIRM), syslog shows BGP_ADJCHANGE from Established to Idle, installed prefixes from that peer drop to zero, and traffic shifts to the remaining ECMP next hops. Flapping shows as established-transitions rising several times within minutes.

## Triage order
1. Check the underlying interface: oper-status DOWN on the local port or the peer port explains the session. Follow the link runbook first.
2. If the link is UP, compare both sides' neighbor config: peer-as must equal the remote AS, both sides must be enabled, and each side must point at the other's interface address.
3. Check the other end: if the peer device stopped reporting telemetry, its management plane may be down while the data plane still forwards.
4. A session stuck in ACTIVE means TCP 179 is not completing: suspect an ACL on the link, a wrong neighbor address, or an MTU problem on the path.
5. Flapping with the link stable points at hold-timer expiry: CPU starvation on either router, control-plane policing drops, or a lossy link (check FCS errors).

## Impact assessment
In a two-spine leaf-spine fabric, one leaf-spine session down halves that leaf's uplink capacity; it is not an outage while the other spine's session holds. Confirm with a forwarding trace from affected leaves that every destination is still delivered over the surviving path, and check utilization on the surviving uplinks, which now carry double load.

## Safe remediation
Do not clear or bounce sessions blindly: a hard reset drops all prefixes from the peer. Prefer fixing the cause (config mismatch, interface). Any config change needs pre-verification that no prefix becomes unreachable, and a commit-confirm window so a mistake reverts itself.
