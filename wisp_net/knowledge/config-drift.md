# Configuration drift

## Definition
The running configuration differs from the approved golden configuration: a manual change, a failed or partial automation run, or an unauthorized change.

## Detection
Compare running config against the golden template per device, field by field. Classify each difference: security-relevant (ACLs, SSH, login banner, NTP, SNMP), routing-relevant (BGP neighbors, export policy, networks), or cosmetic (descriptions).

## Response
Security and routing drift needs an owner and either a revert or an approved update to the golden template. Reverting drift is a change like any other: verify it, simulate it, and apply it with commit-confirm.
