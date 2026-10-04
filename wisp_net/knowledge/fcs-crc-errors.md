# FCS / CRC errors on an interface

## What they mean
in-fcs-errors counts frames whose frame check sequence failed: the bits arrived corrupted. They are a physical-layer problem on the receive side of the port that counts them: optics, fiber, connectors, or the far-end transmitter. They are never caused by congestion.

## Triage order
1. Identify the receiving side. Errors on leaf1 Ethernet49 implicate the path from the peer's transmitter to leaf1's receiver.
2. Read the transceiver receive power (input-power) on the erroring port. Below about -10 dBm on short-reach 100G optics the margin is thin; errors typically begin near -12 dBm and the link drops at loss of signal.
3. Compare with the peer's receive power in the other direction. One direction bad: that fiber strand or that transmitter. Both bad: shared patch panel or a dirty connector pair.
4. Correlate the error rate with time: a rise that follows falling receive power is a degrading optic or fiber; a sudden step with normal power points at a faulty port, cable damage, or a duplex/encoding mismatch.

## Remediation
Clean and reseat connectors, then replace the optic, then the patch cable. While errors persist, draining the link (shutting the interface so ECMP moves traffic to healthy links) is the standard mitigation if the remaining links have headroom. Verify headroom first: draining one of two uplinks doubles load on the other.
