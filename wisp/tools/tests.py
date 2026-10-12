"""Test runner tool — execute pytest and format results for LLM consumption."""



def tool_run_tests(
    files: list[str] | None = None,
    workspace: str = ".",
    timeout: int = 120,
    include_named_tests: bool = True,
) -> str:
    """Run tests for the given files, or all tests if no files specified.

    If *files* is provided, only tests affected by those files are run, and a test file named there is run itself (*include_named_tests*; the auto-test after
    a write turns it off).
    If *files* is empty/None, the entire test suite is run.

    Returns a formatted summary string suitable for LLM consumption.
    """
    try:
        from wisp.test_runner import run_affected_tests, run_all_tests
    except ImportError as exc:
        return f"Test runner not available: {exc}"

    if files:
        summary = run_affected_tests(files, workspace, timeout=timeout, include_named_tests=include_named_tests)
    else:
        summary = run_all_tests(workspace, timeout=timeout)
    return summary.format_for_llm()
