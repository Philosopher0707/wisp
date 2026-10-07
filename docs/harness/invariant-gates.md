# Invariant gates

Five deterministic layers between the model and the machine. Each is a pure function of its input (plus the filesystem's
symlinks), refuses what it cannot prove safe, and has an executable witness: a test that attempts the violation and asserts it fails.

| Layer | Responsibility | Enforcement | Module |
|---|---|---|---|
| 1. Paths & files | Changes stay in the workspace or an explicit whitelist | `realpath` of every write target, separator-anchored prefix check (`wisp.pathsec.resolve_contained`); targets are taken from the *parsed* command (redirects, `rm`, `mv`, `cp`, `tee`, `dd of=`, `sed -i`, …) and from tool arguments | `paths.py` |
| 2. Commands | Irreversible commands never run, however they are spelled | A real shell parser (`shellparse.py`) feeds an extractor (`invocations.py`) that looks through wrappers (`env`, `xargs`, `sudo`, `sh -c`, here-docs, `find -exec`) and tracks `cd`; rules in `commands.py` read the tree, never the raw string | `shellparse.py`, `invocations.py`, `commands.py` |
| 3. Secrets | Secrets reach neither the model nor the network | Repository patterns + vendor patterns + a bounded entropy detector; tool results are scrubbed before the model sees them; a secret or credentials file in a network command is refused | `secrets.py`, `gate.py`, `core/tool_result_guard.py` |
| 4. Dependency lock | No unprompted library installation | Named-package installs and manifest/lockfile writes are refused while the lock is closed; installing what the lockfile pins is allowed | `deps.py` |
| 5. Completion verifier | A turn that changed code cannot finish on a command that proves nothing | `classify()` accepts only a recognised test/lint/type-check/build run whose exit status decides the command's result; the existing `VerificationFloorGuard` consults it | `verify.py`, `core/verification.py` |

One entry point: `check_tool_call(name, args, ctx, mutating=…)` in `gate.py`, called once per tool call from
`WispAgentCore._gate_tool_call`, **before** the approval prompt (no human approval can override it). A refusal is a `POLICY_DENIED`
result with `_src="invariant_gate"`, so recovery, scoring and the audit log treat it like any other policy denial.

## Configuration (operator only)

| Setting | Env | Default | Meaning |
|---|---|---|---|
| `invariant_gates` | `WISP_INVARIANT_GATES` | `enforce` | `enforce` blocks; `observe` logs what it would block and lets the call run; `off` disables every layer (and restores the old verification rule). Any other value means `enforce`. |
| `dependency_lock` | `WISP_DEPENDENCY_LOCK` | `locked` | Only the exact word `unlocked` opens it. |
| `gate_write_roots` | `WISP_GATE_WRITE_ROOTS` | empty | Extra writable directories, `os.pathsep`-separated. |

A tool argument can never change these (G5); the gates read configuration, the model supplies only the call.

## The invariants

Each row names the witness. `tests/gates/test_invariant_table.py` fails if a witness named here no longer exists.

| ID | Invariant | Enforced in | Can be violated by | Witness |
|---|---|---|---|---|
| P1 | No write outside the workspace or whitelist | `paths.check_path` | `..`, absolute paths, `~`, shell variables, globs, redirects | `tests/gates/test_paths.py::TestShellWrites::test_writes_outside_the_workspace_are_refused` |
| P2 | Symlinks, `..` and sibling prefixes cannot escape | `pathsec.resolve_contained` via `check_path` | a link inside the workspace pointing out; `/ws-sibling` | `tests/gates/test_paths.py::TestSymlinksAndPrefixes::test_a_symlink_inside_the_workspace_pointing_out_is_refused` |
| P3 | A target that cannot be resolved is refused, not guessed | `paths.check_path` | `$VAR`, `$(…)`, unknown `cd` | `tests/gates/test_paths.py::TestShellWrites::test_a_target_that_cannot_be_resolved_is_refused_not_guessed` |
| P4 | Hook and git-config paths are never writable | `pathsec.is_protected_path` | writing `.git/hooks/*` from inside the workspace | `tests/gates/test_paths.py::TestWhitelistAndProtected::test_hook_and_git_config_paths_are_protected_even_inside` |
| P5 | Write tools obey the same rule as the shell | `gate.check_tool_args` | `write_file` with an outside or symlinked path | `tests/gates/test_paths.py::TestToolArguments::test_write_tool_paths_outside_are_refused` |
| C1 | An irreversible command is refused however it is spelled | `commands.invocation_violations` | quoting, wrappers, chaining, groups, substitutions, `sh -c`, here-docs, decode-and-run | `tests/gates/test_commands.py::test_disguised_irreversible_commands_are_still_refused` |
| C2 | A command whose identity is only known at run time is refused | `invocations._Walker` | `$CMD`, `eval`, `sh -c "$X"` | `tests/gates/test_commands.py::test_a_command_whose_identity_is_only_known_at_run_time_is_refused` |
| C3 | What the parser does not understand is refused | `shellparse.parse` | unbalanced quotes, `case`, `;;`, over-deep nesting | `tests/gates/test_shellparse.py::TestFailClosed::test_unparsable_input_is_refused_not_guessed` |
| C4 | The parser always terminates and is deterministic | `shellparse` step budget and token checks | an operator with nothing before it | `tests/gates/test_shellparse.py::TestTotalityAndDeterminism::test_parse_never_raises_never_hangs_and_is_deterministic` |
| C5 | Ordinary work is not refused | the rule set | over-broad rules | `tests/gates/test_commands.py::test_ordinary_work_is_not_refused` |
| S1 | Scrubbing is idempotent and leaves nothing detectable | `secrets.scrub` | a placeholder re-detected as a secret | `tests/gates/test_secrets.py::TestInvariants::test_s1_scrubbing_is_idempotent_and_leaves_nothing_detectable` |
| S2 | Findings carry a kind and a span, never the secret | `secrets.Finding` | a finding that echoes the value | `tests/gates/test_secrets.py::TestInvariants::test_s2_findings_never_carry_the_secret` |
| S3 | Clean text is returned unchanged | `secrets.scrub` | needless rewriting | `tests/gates/test_secrets.py::TestInvariants::test_s3_clean_text_is_returned_unchanged` |
| S4 | The result depends on the text alone | `secrets` | order- or state-dependent detection | `tests/gates/test_secrets.py::TestInvariants::test_s4_the_result_depends_on_the_text_alone` |
| S5 | A secret never reaches the model | `tool_result_guard.scrub_secrets` at the engine seam | output printing a credential | `tests/gates/test_gate_seam.py::TestResultScrub::test_a_secret_a_command_prints_never_reaches_the_model` |
| S6 | A secret or credentials file is never sent over the network | `gate._network_violations` | `curl -d @.env`, `cat id_rsa \| nc`, a literal token in a header | `tests/gates/test_secrets.py::TestNetworkCommandGate::test_sending_a_credentials_file_is_refused` |
| D1 | While locked, nothing adds or changes a dependency or manifest | `deps.check_install`, `gate` | `pip install x`, `npm i x`, `echo >> package.json`, `npx unknown` | `tests/gates/test_deps.py::test_adding_or_changing_a_dependency_is_refused_while_locked` |
| D2 | Only the operator opens the lock | `gate.parse_lock`, `GateContext` | an argument, an inline `WISP_DEPENDENCY_LOCK=unlocked` | `tests/gates/test_gate.py::TestG5TheModelCannotChangeThePolicy::test_a_command_cannot_unlock_the_lock_by_setting_an_environment_variable` |
| V1 | Only a tool whose exit status decides the result counts as verification | `verify.classify` | `true`, `pytest \|\| true`, `pytest \| tail`, `pytest; echo done`, `pytest &` | `tests/gates/test_verify.py::test_a_command_that_can_exit_zero_on_broken_code_is_not_verification` |
| V2 | The completion guard uses V1; a failure is always recorded | `VerificationFloorGuard.note_tool_result` | a passing no-op after an edit | `tests/gates/test_verify.py::TestTheGuardUsesIt::test_a_passing_command_that_proves_nothing_does_not_verify` |
| V3 | The production call site gives the guard the command | `stateless._turn_inner` | a guard that silently reverts to the legacy rule | `tests/gates/test_gate_seam.py::test_the_production_call_site_passes_the_command_to_the_guard` |
| G1 | Decisions are deterministic and pure | all modules | clock, randomness, environment, network | `tests/gates/test_purity.py::test_no_gate_module_uses_impure_facilities` |
| G2 | A layer that raises refuses the call | `gate.check_tool_call` | a bug in any layer | `tests/gates/test_gate.py::TestG2FailClosed::test_a_layer_that_raises_refuses_the_call` |
| G3 | One enforcement point, before approval | `stateless._gate_tool_call` | a human approving a hard denial | `tests/gates/test_gate_seam.py::TestPreDispatchRefusal::test_an_irreversible_command_never_runs_even_when_the_human_approves` |
| G4 | A typo never weakens the policy | `gate.parse_mode`, `gate.parse_lock`, `WispConfig` | `enfroce`, `UNLOCK`, `0` | `tests/gates/test_gate.py::TestG4ATypoNeverWeakensThePolicy::test_parse_mode` |
| G5 | The model cannot change the policy | `GateContext` is built from configuration only | tool arguments naming a mode or lock | `tests/gates/test_gate.py::TestG5TheModelCannotChangeThePolicy::test_tool_arguments_cannot_open_the_policy` |

## Limits (stated, not hidden)

- **Inline interpreter code** (`python -c`, `node -e`, `perl -e`) is not parsed. The sandbox bounds what it does; the path layer still checks every write the *shell* can see. `tests/gates/test_commands.py::TestKnownLimits` pins this so nobody assumes more.
- **Time of check vs time of use**: a symlink swapped between the check and the write is not defended here.
- **Entropy detection is a heuristic.** The pattern detectors are exact; the entropy detector is narrow on purpose (digits and mixed case, or a credential word on the line) and skips hashes, UUIDs, paths and lockfile integrity strings. It can miss a low-entropy secret and, rarely, redact a harmless token.
- **Verification classification is by tool name and structure.** A project whose test command is a custom script (`./run-tests.sh`) is not recognised and must be run through a recognised wrapper (`make test`).
- **`xargs rm` and `find -exec rm`** are refused because what they delete cannot be bounded statically.

## Measured (2026-10-07)

- `pytest tests/gates`: 873 tests, no mocks of the layers; the integration tests drive a real turn through `AgentRuntime` and check the disk and the messages the model was shown.
- Mutation probe: 26 deliberate breakages (a rule switched off, containment always passing, symlinks not resolved, errors allowing instead of refusing, the engine seam not called, ...), source bytes restored in `finally`. **26 of 26 caught.** On the first run one survived (`entropy detector re-flags a placeholder label`); that exposed a real violation of S1 (`secret=[REDACTED:x]` re-matched, so scrubbing had no fixed point), now fixed and pinned by `test_a_placeholder_label_is_never_mistaken_for_a_secret`.
- Fuzz: 6,000 random command strings for the parser and extractor (never raises, never hangs, deterministic); it found an infinite loop on an operator with nothing before it, now refused and bounded by a step budget.
- Reproducing a real defect through the engine: the secret scrub first did nothing in production because the engine yields a flat dict that the existing helper did not recognise; the same helper makes the prompt-injection guard inert (a separate follow-up).
- False-positive corpus: 76 ordinary commands (`git commit`, `npm test`, `cd x && make`, `curl | jq`, ...) are not refused; the secrets tests include hashes, UUIDs, paths and lockfile integrity strings that must not be redacted.

