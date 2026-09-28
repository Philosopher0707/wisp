# ACL changes: safety checks

## Before any ACL change
1. Shadowing: a new rule that is fully covered by an earlier rule with a different action never matches. A rule that covers a later rule makes the later rule dead.
2. Leakage: compute what the changed ACL permits between security zones and compare against the segmentation policy (for example PCI or HIPAA zones must not reach general server networks).
3. Collateral damage: an ACL on a fabric link can block routing protocol traffic (BGP TCP 179) and take the session down. Permit control-plane traffic explicitly and early.
4. Blast radius: count the interfaces the change binds to and stay under the change policy's limit.

## Quarantine of a compromised port
Shutting an access port or binding a deny ACL to it isolates one server. Verify the port is an access port with a single attached host, not a fabric uplink, before acting.
