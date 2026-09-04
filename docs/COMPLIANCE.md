# Compliance Evidence Map (M7)

Each control names its artifact and the command that produces it. Auditors
run the commands; nothing here requires code access.

| Control | Artifact | Command |
|---|---|---|
| What version/policies/model/tools produced a change | Repro manifest + span attrs | `wisp trace <id>`, `wisp task export-evidence <id>` |
| Approval was obtained (or bypass attempted and denied) | Approval spans + policy-decision spans | `wisp trace <id>` (kinds `approval`, `policy_decision`) |
| Audit trail intact, no secrets | Hash chain + redaction invariant | `wisp audit verify`, `wisp release diagnostics` |
| Policy authentic and current | Signature + revocation_seq + expiry | `wisp policy verify`, `wisp policy health` |
| Denial explained with owning layer | Provenance + plain-language reason | `wisp policy explain TOOL`, `wisp policy dry-run` |
| Dependencies pinned and licensed | Lock verify + SBOM + license audit | `wisp release verify-deps`, `wisp release sbom`, `wisp release licenses` |
| Release quality gate | Test + lint + eval evidence | `docs/RELEASE.md` evidence gate |
| Crash recovery without repeated writes | Run transitions + idempotency table | `wisp task inspect <id>` (transition history) |
| Extension consent recorded | Consent JSONL | workspace `.wisp/consents.jsonl` |
| Local health | Check list | `wisp release health` |

Data-classification handling: `docs/enterprise-target-architecture.md` §3.
Export tiers: metrics-only default, higher tiers explicit opt-in (M5).
