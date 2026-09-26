"""ADR-0054's instrument — is ADR-0051 R2's population satisfiable?

ADR-0051 R4 requires **≥ 2 capable models** for the enablement measurement. This enumerates
every model the local Ollama daemon serves and classifies each one, so the residual is
**measured** rather than assumed — and re-runnable, so it cannot go stale silently (F75).

A model is **capable** when it can drive Wisp's 42-tool surface. The F8-era measurement
(`PHASE_POST-M13_ADR-0016_LIVE_PROVIDER_MEASUREMENT.md` §4) is the reference: the two local
models produced 0/4 and 1/4 schema-valid tool calls, so they are recorded here as
**degenerate** rather than re-measured.

Run:  env -u PYTHONPATH .venv/bin/python scripts/acceptance_gate_population.py
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

ENDPOINT = "http://127.0.0.1:11434"

#: From `PHASE_POST-M13_ADR-0016_LIVE_PROVIDER_MEASUREMENT.md` §4 — the model that
#: reached 5/5 schema-valid calls against the real tool surface.
CAPABLE = ("nemotron-3-ultra:cloud",)

#: Measured there too: 0/4 and 1/4 schema-valid tool calls. Affordable, but they cannot
#: mutate, so every turn they drive lands INCONCLUSIVE by construction.
DEGENERATE = ("llama3.2:3b", "qwen2.5:0.5b")


def _post(model: str, timeout: float = 60.0) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "stream": False,
        "options": {"num_predict": 16},
    }).encode()
    req = urllib.request.Request(f"{ENDPOINT}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("message", {}).get("content", "")
    except urllib.error.HTTPError as exc:
        try:
            return "ERROR: " + json.loads(exc.read().decode()).get("error", "")[:120]
        except Exception:
            return f"ERROR: HTTP {exc.code}"
    except Exception as exc:                                   # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {str(exc)[:80]}"


def _served() -> list[str]:
    with urllib.request.urlopen(f"{ENDPOINT}/api/tags", timeout=10) as r:
        return [m["name"] for m in json.loads(r.read().decode()).get("models", [])]


def main() -> int:
    print("=" * 96)
    print("ADR-0054 INSTRUMENT — is ADR-0051 R4's population (>= 2 capable models) satisfiable?")
    print("=" * 96)

    try:
        served = _served()
    except Exception as exc:                                   # noqa: BLE001
        print(f"  the daemon is not reachable at {ENDPOINT}: {exc}")
        return 2

    print(f"\ndaemon: {ENDPOINT}   models served: {len(served)}\n")
    hdr = f"{'model':32s} {'class':>11s}  probe"
    print(hdr)
    print("-" * len(hdr))

    capable = degenerate = retired = paywalled = unknown = 0
    for model in sorted(served):
        if model in CAPABLE:
            cls, note = "CAPABLE", "(recorded: 5/5 schema-valid tool calls)"
        elif model in DEGENERATE:
            cls, note = "degenerate", "(recorded: cannot drive the tool surface)"
        else:
            reply = _post(model)
            if reply.startswith("ERROR:"):
                low = reply.lower()
                if "retired" in low:
                    cls, note, retired = "retired", reply[:60], retired + 1
                elif "free usage" in low or "credits" in low:
                    cls, note, paywalled = "paywalled", reply[:60], paywalled + 1
                else:
                    cls, note, unknown = "unknown", reply[:60], unknown + 1
            else:
                cls, note = "affordable?", f"reply={reply[:20]!r} — capability NOT measured"
                unknown += 1
        if cls == "CAPABLE":
            capable += 1
        elif cls == "degenerate":
            degenerate += 1
        print(f"{model:32s} {cls:>11s}  {note}")

    print()
    print("=" * 96)
    print(f"  capable      : {capable}   <- ADR-0051 R4 requires >= 2")
    print(f"  degenerate   : {degenerate}")
    print(f"  retired      : {retired}")
    print(f"  paywalled    : {paywalled}")
    print(f"  unclassified : {unknown}")
    print()
    if capable >= 2:
        print("  => the population IS satisfiable. Re-run ADR-0051 R2's measurement, then")
        print("     ADR-0054's default may be revisited.")
    else:
        print("  => the population is NOT satisfiable: ADR-0051 R4 requires >= 2 capable")
        print("     models and this environment serves exactly one. The acceptance gate")
        print("     therefore ships default OFF, and the enablement decision stays open.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
