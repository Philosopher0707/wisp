# Link congestion and microbursts

## Symptoms
Egress utilization above 80 percent sustained, out-discards increasing on the congested egress port, application latency or retransmits. Averages over minutes can hide microbursts that fill buffers for milliseconds; discards with moderate average utilization are the tell.

## Triage order
1. Locate the congested egress interface and direction: out-discards count on the transmitting side.
2. Check whether ECMP is balanced: in a healthy two-spine fabric both uplinks of a leaf carry similar load. Imbalance suggests a failed or drained parallel link, or elephant flows hashing onto one path.
3. Identify top talkers from flow records (IPFIX/sFlow) for the congested path: source, destination, destination port.
4. Check for a recent topology change (a link or session down) that concentrated traffic.

## Remediation options
Restore the failed parallel path; rebalance by adjusting BGP policy or path weights; rate-limit or reschedule a bulk flow; add capacity. Every traffic-engineering change must be simulated first to show the new distribution does not congest a different link.
