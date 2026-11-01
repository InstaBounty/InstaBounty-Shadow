#!/usr/bin/env python3
"""Seed Aider with a bounded set of editable repository files."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Callable

MAX_SEED_FILES = 16
WORKFLOW_SCAFFOLD = ".github/workflows/deploy-test-s3.yml"
NEXT_STATIC_CONFIG = "next.config.mjs"

_PRIORITY_FILES = (
    "package.json",
    "pnpm-workspace.yaml",
    "tsconfig.json",
    "next.config.ts",
    "next.config.mjs",
    "next.config.js",
    "vite.config.ts",
    "vite.config.js",
    "README.md",
    "app/layout.tsx",
    "app/page.tsx",
    "src/app/layout.tsx",
    "src/app/page.tsx",
)


def _tracked_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def _is_s3_static_task(task: str) -> bool:
    lowered = task.lower()
    return "s3" in lowered and ("static" in lowered or "static site" in lowered or "static hosting" in lowered)


def _needs_workflow_scaffold(task: str) -> bool:
    lowered = task.lower()
    asks_for_workflow = "github actions" in lowered or "workflow" in lowered
    return asks_for_workflow and "s3" in lowered


def _ensure_workflow_scaffold(repo: Path, task: str) -> str | None:
    if not _needs_workflow_scaffold(task):
        return None

    existing = [
        path
        for path in _tracked_files(repo)
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
    ]
    if existing:
        return None

    path = repo / WORKFLOW_SCAFFOLD
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# AI scaffold: replace this placeholder with the requested S3 test deployment workflow.\n",
            encoding="utf-8",
            newline="",
        )
        print(f"Aider workflow scaffold created: {WORKFLOW_SCAFFOLD}")
    return WORKFLOW_SCAFFOLD


def _ensure_next_static_config(repo: Path, task: str) -> str | None:
    if not _is_s3_static_task(task):
        return None

    tracked = set(_tracked_files(repo))
    existing = next(
        (path for path in ("next.config.ts", "next.config.mjs", "next.config.js") if path in tracked),
        None,
    )
    if existing:
        return existing

    path = repo / NEXT_STATIC_CONFIG
    if not path.exists():
        path.write_text(
            "/** @type {import('next').NextConfig} */\n"
            "const nextConfig = {\n"
            "  output: 'export',\n"
            "};\n\n"
            "export default nextConfig;\n",
            encoding="utf-8",
            newline="",
        )
        print(f"Aider static Next.js scaffold created: {NEXT_STATIC_CONFIG}")
    return NEXT_STATIC_CONFIG


def _static_task_guidance(task: str) -> str:
    if not _is_s3_static_task(task):
        return task
    guidance = (
        "\n\nImplementation guidance for this static S3 task:\n"
        "- S3 cannot execute Next.js route handlers or other server-only code.\n"
        "- Dug currently has app/api/traces/[traceId]/route.ts and app/page.tsx calls /api/traces/<id>.\n"
        "- Make the browser use a configurable public API base URL (for example NEXT_PUBLIC_DUG_API_BASE_URL) "
        "instead of depending on the local Next.js API route in the S3 build.\n"
        "- Keep local development sensible where possible, but the static export must not depend on a server route.\n"
        "- Ensure next build produces the out/ directory, and make the deployment workflow sync out/ to S3.\n"
        "- Update README.md with the required AWS role, bucket/repository variables, API base URL, and deployment steps.\n"
        "- Update or add tests for any changed frontend URL behaviour.\n"
    )
    return task + guidance


def seed_files(repo: Path, task: str = "") -> list[str]:
    tracked = _tracked_files(repo)
    tracked_set = set(tracked)
    selected: list[str] = []

    workflow_scaffold = _ensure_workflow_scaffold(repo, task)
    if workflow_scaffold is not None:
        selected.append(workflow_scaffold)

    next_config = _ensure_next_static_config(repo, task)
    if next_config is not None and next_config not in selected:
        selected.append(next_config)

    for path in _PRIORITY_FILES:
        if path in tracked_set and path not in selected:
            selected.append(path)

    workflows = sorted(
        path for path in tracked if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
    )
    for path in workflows:
        if path not in selected:
            selected.append(path)

    if len(selected) < MAX_SEED_FILES:
        source_candidates = [
            path
            for path in tracked
            if path.endswith((".ts", ".tsx", ".js", ".jsx", ".py"))
            and not path.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", "_test.py"))
            and not path.startswith(("tests/", "test/"))
        ]
        for path in source_candidates:
            if path not in selected:
                selected.append(path)
            if len(selected) >= MAX_SEED_FILES:
                break

    return selected[:MAX_SEED_FILES]


def _is_aider_command(command: Any) -> bool:
    if not isinstance(command, (list, tuple)) or not command:
        return False
    executable = Path(str(command[0])).name.lower()
    return executable in {"aider", "aider.exe", "aider.cmd"}


def apply(namespace: dict[str, Any], argv: list[str] | None = None) -> None:
    """Patch the runner's Popen only while run_aider executes."""
    original_run_aider: Callable[..., int] = namespace["run_aider"]
    subprocess_module = namespace["subprocess"]
    original_popen = subprocess_module.Popen

    def run_aider_with_seed(repo: Path, **kwargs: Any) -> int:
        task = str(kwargs.get("task", ""))
        seeds = seed_files(Path(repo), task)
        if seeds:
            print("Aider editable seed files: " + ", ".join(seeds))
        else:
            print("Aider editable seed files: none detected; using repository map only.")

        guided_kwargs = dict(kwargs)
        guided_kwargs["task"] = _static_task_guidance(task)

        def seeded_popen(command: Any, *args: Any, **popen_kwargs: Any):
            if seeds and _is_aider_command(command):
                command = [*command, *seeds]
            return original_popen(command, *args, **popen_kwargs)

        subprocess_module.Popen = seeded_popen
        try:
            return original_run_aider(repo, **guided_kwargs)
        finally:
            subprocess_module.Popen = original_popen

    namespace["run_aider"] = run_aider_with_seed


def _self_test() -> None:
    assert _is_aider_command(["aider.exe", "--version"])
    assert not _is_aider_command(["git", "status"])
    assert _needs_workflow_scaffold("Add a GitHub Actions workflow to deploy to S3")
    assert not _needs_workflow_scaffold("Update the health endpoint")
    assert _is_s3_static_task("Deploy the static site to an S3 bucket")
    assert "S3 cannot execute" in _static_task_guidance("Deploy a static site to S3")
    assert MAX_SEED_FILES > 0


if __name__ == "__main__":
    _self_test()
