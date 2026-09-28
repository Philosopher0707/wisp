# Interface flapping

## Symptoms
Repeated LINK_DOWN / LINK_UP syslog pairs for the same port, BGP sessions over it cycling, and route churn felt across the fabric as ECMP sets shrink and grow.

## Triage
Check receive power and FCS errors for a marginal optic, check both ends for the same pattern (a flap seen on both sides is the link, on one side only may be that port), and correlate flap timestamps with maintenance or physical work.

## Mitigation
A flapping link hurts more than a down link because every transition triggers reconvergence. Administratively shutting it (draining) stops the churn; verify spare capacity first and record the change so it is reverted after the physical fix. Dampening can limit the blast radius while the root cause is found.
