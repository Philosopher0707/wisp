# Change management for automated network changes

## Principles
Every change is declarative (desired state, not commands), idempotent (applying it twice changes nothing the second time), verified before execution, and reversible.

## Pre-execution gates
1. Formal checks on the digital twin: no forwarding loop, no new blackhole, no ACL shadowing or leakage, required isolation between zones still holds.
2. Simulation on an isolated copy of the topology: control-plane convergence completes and traffic still reaches every destination.
3. Policy: blast radius within limits (for example at most 10 ports changed at once), not inside a prohibited change window, and agent confidence at or above the threshold (for example 0.85) or a human approves.

## Execution
Commit with confirm: the device applies the change and reverts it automatically unless confirmed within a grace period. During the grace period watch health: BGP flap rate, packet loss on the touched interfaces, management reachability. Confirm only when healthy; otherwise roll back immediately.

## Record
Log the trigger, the reasoning, the proposed and the applied diff, verification and simulation results, who or what approved it, and the post-change verification, in an append-only audit ledger.
