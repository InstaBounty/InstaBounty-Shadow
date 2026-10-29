#!/usr/bin/env python3
"""Bound reviewer evidence so the implementation diff stays inside the model context window."""

from __future__ import annotations

from pathlib import Path

MAX_REVIEW_DIFF = 16_000
MAX_REPOSITORY_CONTEXT = 2_000


def apply(namespace: dict, argv: list[str]) -> None:
    agent_error = namespace["AgentError"]
    original_working_diff = namespace.get("working_diff")
    original_repository_file_context = namespace.get("repository_file_context")

    if not callable(original_working_diff) or not callable(original_repository_file_context):
        raise agent_error("Reviewer context bridge could not locate reviewer evidence helpers.")

    def bounded_working_diff(repo: Path, base: str) -> str:
        diff = original_working_diff(repo, base)
        if len(diff) <= MAX_REVIEW_DIFF:
            return diff
        # Keep the tail because it contains the newest hunks and, after prompt assembly,
        # remains closest to the end of the reviewer context window.
        return (
            f"... diff truncated to final {MAX_REVIEW_DIFF} characters for reviewer context ...\n"
            + diff[-MAX_REVIEW_DIFF:]
        )

    def bounded_repository_file_context(repo: Path, limit: int = 400) -> str:
        context = original_repository_file_context(repo, limit)
        if len(context) <= MAX_REPOSITORY_CONTEXT:
            return context
        return context[:MAX_REPOSITORY_CONTEXT] + "\n... repository context truncated ..."

    namespace["working_diff"] = bounded_working_diff
    namespace["repository_file_context"] = bounded_repository_file_context

    run_reviewer = namespace.get("run_reviewer")
    if not callable(run_reviewer):
        raise agent_error("Reviewer context bridge could not locate run_reviewer.")
    run_reviewer.__globals__["working_diff"] = bounded_working_diff
    run_reviewer.__globals__["repository_file_context"] = bounded_repository_file_context

    print(
        "Reviewer context bridge active: "
        f"diff<= {MAX_REVIEW_DIFF} chars, repository_context<= {MAX_REPOSITORY_CONTEXT} chars."
    )


def _self_test() -> None:
    assert MAX_REVIEW_DIFF < 32_000
    assert MAX_REPOSITORY_CONTEXT < MAX_REVIEW_DIFF


if __name__ == "__main__":
    _self_test()
    print("review context bridge self-test passed")
