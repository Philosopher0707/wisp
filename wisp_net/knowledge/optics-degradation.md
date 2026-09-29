# Optical transceiver degradation

## Signals
Transceiver input-power (dBm) trending down over hours or days, a receive-power-low alarm (RX_POWER_LOW), then FCS errors, then loss of signal and the link going down. The trend is the early warning; errors are the late one.

## Predicting time to failure
Fit a line to input-power over a recent window. The slope in dB per hour and the distance to the error onset (about -12 dBm) and loss-of-signal (about -28 dBm) thresholds give an estimated time to impact. A steady slope suggests ageing laser or contamination; a step change suggests a physical disturbance (bent fiber, reseated connector).

## Action
Schedule optic replacement inside a maintenance window before the projected error onset. If the projection is shorter than the next window, drain the link in advance: shut the interface after verifying the fabric has enough spare capacity, so failure happens without traffic on it.
