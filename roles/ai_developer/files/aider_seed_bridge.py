#!/usr/bin/env python3
"""Seed Aider with a bounded set of editable repository files."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Callable

MAX_SEED_FILES = 16
WORKFLOW_SCAFFOLD = ".github/workflows/deploy-test-s3.yml"

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


def _needs_workflow_scaffold(task: str) -> bool:
    lowered = task.lower()
    asks_for_workflow = "github actions" in lowered or "workflow" in lowered
    asks_for_s3 = "s3" in lowered
    return asks_for_workflow and asks_for_s3


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


def seed_files(repo: Path, task: str = "") -> list[str]:
    tracked = _tracked_files(repo)
    tracked_set = set(tracked)
    selected: list[str] = []

    scaffold = _ensure_workflow_scaffold(repo, task)
    if scaffold is not None:
        selected.append(scaffold)

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

        def seeded_popen(command: Any, *args: Any, **popen_kwargs: Any):
            if seeds and _is_aider_command(command):
                command = [*command, *seeds]
            return original_popen(command, *args, **popen_kwargs)

        subprocess_module.Popen = seeded_popen
        try:
            return original_run_aider(repo, **kwargs)
        finally:
            subprocess_module.Popen = original_popen

    namespace["run_aider"] = run_aider_with_seed


def _self_test() -> None:
    assert _is_aider_command(["aider.exe", "--version"])
    assert not _is_aider_command(["git", "status"])
    assert _needs_workflow_scaffold("Add a GitHub Actions workflow to deploy to S3")
    assert not _needs_workflow_scaffold("Update the health endpoint")
    assert MAX_SEED_FILES > 0


if __name__ == "__main__":
    _self_test()
