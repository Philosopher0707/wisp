"""Traffic engineering: where the load is, what drives it, and what a drain would do.

The assessment ranks egress ports by utilization, names the heaviest flows toward the
destinations behind each hot port, and for every hot fabric link simulates draining it on
a clone (the constraint: no port may end up above the limit). In a leaf-spine fabric a
drain usually concentrates load on the parallel link, and the simulation says so rather
than the heuristic guessing. The blueprint's learned (PPO) and CSP optimizers are future
work behind this interface.
"""

from __future__ import annotations

from typing import Any

from wisp_net.sim.config import apply_set
from wisp_net.sim.network import SimNetwork

HOT = 0.8


def _egress_utilization(net: SimNetwork) -> dict[str, float]:
    return {f"{d.name}:{i.name}": i.tx_bps / i.speed_bps
            for d in net.devices.values() for i in d.interfaces.values() if i.speed_bps and i.oper_up}


def te_assess(service: Any, limit: float = HOT, settle_s: float = 10.0) -> dict[str, Any]:
    view = service.collector.last_view.get("interfaces", {})
    ranked = sorted(((f"{dev}:{port}", m.get("tx_util", 0.0), m.get("out_discards_per_s", 0.0), m.get("kind"))
                     for dev, ports in view.items() for port, m in ports.items()), key=lambda r: -r[1])
    hot = [r for r in ranked if r[1] >= limit or r[2] > 0]
    flows = service.collector.top_flows(service.net.now - 300, 5)
    candidates = []
    for port, util, _, kind in hot:
        if kind != "fabric":
            continue
        dev, _, ifname = port.partition(":")
        clone = service.net.clone()
        path = f"/interfaces/interface[name={ifname}]/config/enabled"
        clone.apply_config(dev, apply_set(clone.devices[dev].config, [(path, False)], []), user="te-assess")
        for _ in range(int(settle_s)):
            clone.step(1.0)
        after = _egress_utilization(clone)
        worst_port, worst = max(after.items(), key=lambda kv: kv[1]) if after else ("", 0.0)
        candidates.append({"action": f"drain {port}", "max_egress_util_after": round(worst, 3),
                           "worst_port_after": worst_port, "meets_limit": worst < limit})
    return {"limit": limit,
            "top_ports": [{"port": p, "tx_util": round(u, 3), "discards_per_s": round(d, 1), "kind": k}
                          for p, u, d, k in ranked[:10]],
            "hot_ports": [p for p, *_ in hot], "top_flows": flows, "drain_candidates": candidates,
            "note": ("access-port congestion (incast toward servers) is not fixed by moving fabric load; "
                     "it needs rate limiting or rescheduling at the sources in top_flows")
            if any(k != "fabric" for *_, k in hot) else ""}
