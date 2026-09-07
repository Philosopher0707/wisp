#!/usr/bin/env bash
# CLI surface audit (GH#11): hermetic env, full 7-group suite, Markdown grid.
#
# Usage:
#   ./scripts/audit_cli_surface.sh [--keep] [-- <pytest args>]
#
# --keep preserves the probe ROOT for inspection (default: tmp dir, kept on
# failure, removed on success). Extra args after -- pass through to pytest.
set -u

KEEP=0
if [[ "${1:-}" == "--keep" ]]; then KEEP=1; shift; fi
if [[ "${1:-}" == "--" ]]; then shift; fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUDIT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/wisp-cli-audit-XXXXXX")"
SITE_PACKAGES="$(python3 -c 'import sys; print(":".join(p for p in sys.path if "site-packages" in p))')"

export HOME="$AUDIT_ROOT/home"
export PYTHONPATH="$REPO:$SITE_PACKAGES"
export WISP_PROVIDER=mock
export TERM="${TERM:-xterm-256color}"
unset WISP_API_KEY

mkdir -p "$HOME"

echo "# CLI Surface Verification Grid"
echo
echo "| # | Surface | Implementation | Status |"
echo "|---|---------|----------------|--------|"
echo "| 1 | --version/--help/flags | wisp/__main__.py:main | pytest |"
echo "| 2 | check/models/config | wisp/__main__.py:cmd_check/cmd_models/cmd_config | pytest |"
echo "| 3 | run/print/quiet | wisp/__main__.py:cmd_run/cmd_print | pytest |"
echo "| 4 | repl/tui | wisp/cli/repl.py, wisp/entry.py:run_mode | pytest (pty) |"
echo "| 5 | session * | wisp/__main__.py:cmd_session_* + wisp/infra/store.py | pytest (+1 xfail GH#12) |"
echo "| 6 | skills/bench | wisp/__main__.py:cmd_skills, wisp/benchmark/cli.py | pytest |"
echo "| 7 | agents/swarm | wisp/multi_agent/cli.py:cmd_agents_*/cmd_swarm | pytest |"
echo
echo "AUDIT_ROOT=$AUDIT_ROOT HOME=$HOME WISP_PROVIDER=$WISP_PROVIDER"
echo

set +e
python3 -m pytest "$REPO/tests/test_cli_surface_e2e.py" -q "$@" 2>&1 | tail -15
RC=${PIPESTATUS[0]}
set -e

echo
if [[ $RC -eq 0 ]]; then
  echo "RESULT: PASS"
  if [[ $KEEP -eq 0 ]]; then rm -rf "$AUDIT_ROOT"; echo "(probe root removed)"; fi
else
  echo "RESULT: FAIL (rc=$RC) — probe root kept: $AUDIT_ROOT"
fi
exit $RC
