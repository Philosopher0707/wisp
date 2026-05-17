"""Task templates for common long-horizon operations.

Templates provide pre-built plans with known steps for common tasks,
reducing the need for the agent to generate plans from scratch.

Usage:
    from wisp.long_horizon.templates import apply_template
    steps = apply_template("migrate_flask_to_fastapi", goal="Migrate my app")
"""

from __future__ import annotations

from wisp.long_horizon.state import Step


# ── Template registry ──────────────────────────────────────────────

TEMPLATES: dict[str, list[Step]] = {
    "migrate_flask_to_fastapi": [
        Step(id="step-1", description="Audit current Flask routes, models, and dependencies"),
        Step(id="step-2", description="Set up FastAPI project structure and dependencies"),
        Step(id="step-3", description="Migrate Flask models to SQLAlchemy 2.0 async models"),
        Step(id="step-4", description="Convert Flask routes to FastAPI endpoints with Pydantic schemas"),
        Step(id="step-5", description="Migrate Flask-Login auth to FastAPI dependency-based auth"),
        Step(id="step-6", description="Update configuration and environment handling"),
        Step(id="step-7", description="Write tests for FastAPI endpoints"),
        Step(id="step-8", description="Run full test suite and fix regressions"),
        Step(id="step-9", description="Update documentation and deployment config"),
    ],
    "add_authentication": [
        Step(id="step-1", description="Design auth flow (JWT vs session vs OAuth)"),
        Step(id="step-2", description="Implement user model with password hashing"),
        Step(id="step-3", description="Create login/register endpoints"),
        Step(id="step-4", description="Add token generation and validation"),
        Step(id="step-5", description="Protect routes with auth middleware/dependencies"),
        Step(id="step-6", description="Add refresh token mechanism"),
        Step(id="step-7", description="Write auth tests"),
        Step(id="step-8", description="Update API documentation"),
    ],
    "refactor_module": [
        Step(id="step-1", description="Analyze current module structure and dependencies"),
        Step(id="step-2", description="Identify code smells and anti-patterns"),
        Step(id="step-3", description="Extract interfaces and abstractions"),
        Step(id="step-4", description="Split large functions into smaller units"),
        Step(id="step-5", description="Update imports and fix circular dependencies"),
        Step(id="step-6", description="Run tests after each change"),
        Step(id="step-7", description="Add missing type hints and docstrings"),
        Step(id="step-8", description="Run linter and fix style issues"),
    ],
    "add_tests": [
        Step(id="step-1", description="Identify untested critical paths"),
        Step(id="step-2", description="Set up test fixtures and mocks"),
        Step(id="step-3", description="Write unit tests for pure functions"),
        Step(id="step-4", description="Write integration tests for API endpoints"),
        Step(id="step-5", description="Add edge case and error handling tests"),
        Step(id="step-6", description="Set up test coverage reporting"),
        Step(id="step-7", description="Run full test suite and fix failures"),
    ],
    "dockerize": [
        Step(id="step-1", description="Analyze project dependencies and runtime requirements"),
        Step(id="step-2", description="Create Dockerfile with multi-stage build"),
        Step(id="step-3", description="Add .dockerignore for build optimization"),
        Step(id="step-4", description="Create docker-compose.yml for local development"),
        Step(id="step-5", description="Configure environment variables for containers"),
        Step(id="step-6", description="Add health checks and graceful shutdown"),
        Step(id="step-7", description="Test build and run locally"),
        Step(id="step-8", description="Update CI/CD for Docker builds"),
    ],
    "setup_ci_cd": [
        Step(id="step-1", description="Choose CI platform (GitHub Actions / GitLab CI / etc)"),
        Step(id="step-2", description="Create basic workflow: lint → test → build"),
        Step(id="step-3", description="Add matrix testing for multiple Python/Node versions"),
        Step(id="step-4", description="Configure caching for dependencies"),
        Step(id="step-5", description="Add security scanning (SAST, dependency check)"),
        Step(id="step-6", description="Set up artifact publishing"),
        Step(id="step-7", description="Add deployment stage with environment protection"),
        Step(id="step-8", description="Test pipeline with a PR"),
    ],
}


def match_template(goal: str) -> str | None:
    """Match a user goal to a known template.

    Returns the template key if matched, None otherwise.
    """
    goal_lower = goal.lower()

    # Direct keyword matching
    if any(kw in goal_lower for kw in ("flask", "fastapi")) and "migrat" in goal_lower:
        return "migrate_flask_to_fastapi"
    if any(kw in goal_lower for kw in ("refactor", "restructure", "clean up")):
        return "refactor_module"
    if any(kw in goal_lower for kw in ("auth", "login", "oauth", "jwt", "password")):
        return "add_authentication"
    if any(kw in goal_lower for kw in ("test", "testing", "coverage")) and "add" in goal_lower:
        return "add_tests"
    if any(kw in goal_lower for kw in ("docker", "container", "dockerize")):
        return "dockerize"
    if any(kw in goal_lower for kw in ("ci/cd", "github action", "gitlab ci", "pipeline")):
        return "setup_ci_cd"

    return None


def apply_template(template_key: str, goal: str) -> list[Step]:
    """Apply a template, customizing step descriptions with the goal.

    Returns a copy of the template steps with descriptions tailored
    to the specific goal.
    """
    template = TEMPLATES.get(template_key, [])
    steps = []
    for step in template:
        # Customize description with goal context
        desc = step.description
        if "{goal}" in desc:
            desc = desc.replace("{goal}", goal)
        steps.append(Step(
            id=step.id,
            description=desc,
            dependencies=step.dependencies.copy(),
            parallel_group=step.parallel_group,
        ))
    return steps


def list_templates() -> list[dict]:
    """Return metadata for all available templates."""
    return [
        {
            "key": key,
            "name": key.replace("_", " ").title(),
            "steps": len(steps),
        }
        for key, steps in TEMPLATES.items()
    ]
