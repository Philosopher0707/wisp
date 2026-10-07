"""Background jobs: shell commands that outlive the call that started them (docs/harness/background-jobs-design.md).

    store       the on-disk job store and its reader (workspace-bound ids, bounded, never reports success it did not see)
    procs       process-tree helpers (pid liveness with start time, descendants, SIGTERM then SIGKILL)
    spawn       the ONE place a job is started (BJ1)
    supervisor  the detached process that owns one job and runs it through `run_bash_confined`
"""
