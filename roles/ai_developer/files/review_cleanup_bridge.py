#!/usr/bin/env python3
"""Runtime bridge for deterministic GitHub review cleanup.

Loaded by the ai-dev Windows wrapper after ai_developer.py. It keeps the main
runner focused on orchestration while enforcing mechanical cleanup without
asking the coding model to perform Git bookkeeping.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

REMOVAL_TERMS = (
    "remove",
    "removed",
    "revert",
    "restore",
    "drop",
    "unrelated",
    "do not modify",
    "should not be changed",
)

DEFAULT_HOUSEKEEPING_FILES = {
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".aider.conf.yml",
    ".aider.model.metadata.json",
    ".aider.model.settings.yml",
}


def _argument(argv: list[str], name: str, default: str | None = None) -> str | None:
    try:
        index = argv.index(name)
    except ValueError:
        return default
    return argv[index + 1] if index + 1 < len(argv) else default


def removal_paths(requirements: list[dict[str, str]], files: list[str]) -> list[tuple[str, str]]:
    """Return (path, requirement-id) pairs explicitly requested for removal/revert."""
    matched: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in requirements:
        lowered = item.get("text", "").lower()
        if not any(term in lowered for term in REMOVAL_TERMS):
            continue
        requirement_id = item.get("id", "F?")
        for path in files:
            normalized = path.lower()
            basename = Path(path).name.lower()
            if (normalized in lowered or basename in lowered) and path not in seen:
                matched.append((path, requirement_id))
                seen.add(path)
    return matched


def housekeeping_paths(task: str, files: list[str], housekeeping: set[str]) -> list[str]:
    """Return unrelated tooling files that should never leak into feature work."""
    original_task = task.split("GitHub review feedback", 1)[0].lower()
    matched: list[str] = []
    for path in files:
        normalized = path.replace("\\", "/")
        lowered = normalized.lower()
        is_housekeeping = (
            normalized in housekeeping
            or lowered.startswith(".aider")
            or lowered.startswith(".vscode/")
            or lowered.startswith(".idea/")
        )
        if not is_housekeeping:
            continue
        basename = Path(normalized).name.lower()
        if lowered in original_task or basename in original_task:
            continue
        matched.append(normalized)
    return matched


def _net_working_diff(globals_dict: dict[str, Any], repo: Path, base: str) -> str:
    git = globals_dict["git"]
    git(repo, "add", "-N", "--", ".", check=False)
    diff = git(repo, "diff", base, "--", check=False).stdout
    limit = globals_dict["MAX_DIFF"]
    if len(diff) > limit:
        return diff[:limit] + f"\n... diff truncated at {limit} characters ..."
    return diff


def _net_changed_files(globals_dict: dict[str, Any], repo: Path, base: str) -> list[str]:
    git = globals_dict["git"]
    git(repo, "add", "-N", "--", ".", check=False)
    output = git(repo, "diff", "--name-only", base, "--", check=False).stdout
    return sorted({line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()})


def _restore_path(globals_dict: dict[str, Any], repo: Path, base: str, path: str) -> None:
    git = globals_dict["git"]
    exists_in_base = git(repo, "cat-file", "-e", f"{base}:{path}", check=False).returncode == 0
    if exists_in_base:
        git(repo, "restore", "--source", base, "--staged", "--worktree", "--", path, check=False)
        return

    git(repo, "rm", "-f", "--cached", "--ignore-unmatch", "--", path, check=False)
    target = repo / path
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    else:
        target.unlink(missing_ok=True)


def apply(namespace: dict[str, Any], argv: list[str]) -> None:
    """Patch the loaded runner with net-diff semantics and deterministic cleanup."""
    globals_dict = namespace["command_develop"].__globals__
    base = _argument(argv, "--base", "main") or "main"

    def working_diff(repo: Path, requested_base: str) -> str:
        return _net_working_diff(globals_dict, repo, requested_base)

    def changed_files(repo: Path, requested_base: str) -> list[str]:
        return _net_changed_files(globals_dict, repo, requested_base)

    original_quality = globals_dict["deterministic_quality_issues"]

    def deterministic_quality_issues(
        repo: Path,
        task: str,
        files: list[str],
        *,
        require_coverage: bool,
    ) -> list[str]:
        requirements = globals_dict["github_review_requirements"](task)
        restores = removal_paths(requirements, files)
        restored: set[str] = set()

        for path, requirement_id in restores:
            _restore_path(globals_dict, repo, base, path)
            restored.add(path)
            print(
                f"Deterministic cleanup: restored {path} to {base} "
                f"for GitHub review feedback {requirement_id}."
            )

        housekeeping = set(globals_dict.get("HOUSEKEEPING_FILES", set())) | DEFAULT_HOUSEKEEPING_FILES
        current_files = _net_changed_files(globals_dict, repo, base)
        for path in housekeeping_paths(task, current_files, housekeeping):
            if path in restored:
                continue
            _restore_path(globals_dict, repo, base, path)
            print(f"Deterministic cleanup: removed unrelated housekeeping change {path}.")

        files = _net_changed_files(globals_dict, repo, base)
        return original_quality(
            repo,
            task,
            files,
            require_coverage=require_coverage,
        )

    globals_dict["working_diff"] = working_diff
    globals_dict["changed_files"] = changed_files
    globals_dict["deterministic_quality_issues"] = deterministic_quality_issues


def _self_test() -> None:
    requirements = [
        {"id": "F5", "text": ".gitignore adds .aider*, which is unrelated and should be reverted."},
        {"id": "F6", "text": "Keep src/gateway/handler.py unchanged."},
    ]
    assert removal_paths(requirements, [".gitignore", "src/gateway/handler.py"]) == [(".gitignore", "F5")]
    assert housekeeping_paths(
        "Implement GET /ping",
        [".gitignore", ".aider.input.history", "src/gateway/ping.py"],
        DEFAULT_HOUSEKEEPING_FILES,
    ) == [".gitignore", ".aider.input.history"]
    assert housekeeping_paths(
        "Update .gitignore for Aider",
        [".gitignore"],
        DEFAULT_HOUSEKEEPING_FILES,
    ) == []
    print("Deterministic review cleanup self-test passed.")


if __name__ == "__main__":
    _self_test()
