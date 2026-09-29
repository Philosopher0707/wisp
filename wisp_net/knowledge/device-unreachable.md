# Device management plane unreachable

## Symptoms
gNMI requests to a device time out and its telemetry stops, while its neighbors still show their links to it UP and BGP sessions to it ESTABLISHED.

## Interpretation
The management plane (out-of-band network, gNMI server, control CPU) failed but the data plane may still forward. Do not assume the device is down: check the neighbors' view. Links up and sessions established from the neighbors mean traffic still flows through it.

## Action
Treat the device's last known state as stale. Do not push changes to it or through automation that depends on reaching it. Investigate the out-of-band path and the device's control plane. If neighbors also lost it, handle it as a device failure.
