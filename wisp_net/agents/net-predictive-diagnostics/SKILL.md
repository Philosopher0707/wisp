---
name: net-predictive-diagnostics
description: Hardware and link-health agent for the wisp-net network. Use when a subtask is about optics, receive power, FCS/CRC errors, interface flaps, or predicting a failure before it happens.
---

# Predictive diagnostics

## Evidence
1. `net_optics_forecast`: falling optics, with slope, fit (R²), and minutes to errors and to loss of signal.
2. `net_error_correlation` for any port with FCS errors: optics or something else?
3. `net_interfaces` and `net_events` for the port: flaps, rx-power alarms, FCS bursts.
4. `net_knowledge` "optics degradation" / "FCS errors": cite the runbook section you apply.

## Reasoning
- A falling optic with a good fit (R² ≥ 0.8) is predictable. If loss of signal is projected inside the next maintenance window, recommend draining the link **before** it fails, and replacing the optic.
- Errors with steady receive power are not optics: point at the port, the cable or the far-end transmitter.
- A flapping link hurts more than a down one. Recommend draining it if the fabric has another uplink (the compiler refuses a drain that would isolate a device).

## Output
The failing component, with evidence; the time to impact; the intent (usually `drain_link`); the confidence; the physical action for a human (the optic or cable to replace).
