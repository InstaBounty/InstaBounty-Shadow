#!/usr/bin/env python3
"""Aider-backed local development runner for the InstaBounty Shadow workstation."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib import error, request

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_DEVELOPER_MODEL = "qwen2.5-coder:7b"
DEFAULT_REVIEWER_MODEL = "gemma3:4b"
DEFAULT_AIDER_COMMAND = "aider"
PROTECTED_BRANCHES = {"main", "master"}
MAX_DIFF = 120_000


class AgentError(RuntimeError):
    pass


def run_process(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = 600,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
        env=env,
    )
    if check and result.returncode != 0:
        raise AgentError(
            f"Command failed ({result.returncode}): {' '.join(args)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_process(["git", *args], repo, check=check)


def ensure_git_repo(repo: Path) -> Path:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise AgentError(f"Repository directory does not exist: {repo}")
    result = git(repo, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise AgentError(f"Not a Git repository: {repo}")
    top = Path(result.stdout.strip()).resolve()
    if top != repo:
        raise AgentError(f"Use the repository root, not a subdirectory. Repository root is: {top}")
    return repo


def ensure_clean(repo: Path) -> None:
    status = git(repo, "status", "--porcelain").stdout.strip()
    if status:
        raise AgentError(
            "Repository has uncommitted changes. Commit/stash them before starting the AI developer.\n"
            + status
        )


def slugify(value: str, limit: int = 36) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:limit].rstrip("-") or "change"


def create_work_branch(repo: Path, base: str, branch: str | None, task: str) -> str:
    if not base:
        raise AgentError("A base branch is required.")
    git(repo, "show-ref", "--verify", f"refs/heads/{base}")
    git(repo, "switch", base)

    branch_name = branch or f"ai/{slugify(task)}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if branch_name.lower() in PROTECTED_BRANCHES:
        raise AgentError(f"Refusing to use protected branch: {branch_name}")
    if not (branch_name.startswith("ai/") or branch_name.startswith("feature/")):
        raise AgentError("AI work branches must start with 'ai/' or 'feature/'.")
    if git(repo, "show-ref", "--verify", f"refs/heads/{branch_name}", check=False).returncode == 0:
        raise AgentError(f"Branch already exists: {branch_name}")

    git(repo, "switch", "-c", branch_name)
    return branch_name


def working_diff(repo: Path, base: str) -> str:
    git(repo, "add", "-N", "--", ".", check=False)
    diff = git(repo, "diff", check=False).stdout
    if not diff.strip():
        diff = git(repo, "diff", f"{base}...HEAD", check=False).stdout
    if len(diff) > MAX_DIFF:
        return diff[:MAX_DIFF] + f"\n... diff truncated at {MAX_DIFF} characters ..."
    return diff


def detected_test_command(repo: Path) -> str | None:
    if (repo / "pyproject.toml").is_file():
        return "uv run pytest"
    if (repo / "pnpm-lock.yaml").is_file():
        return "pnpm test"
    if (repo / "package.json").is_file():
        return "npm test"
    if (repo / "foundry.toml").is_file():
        return "forge test"
    return None


def aider_environment(ollama_url: str) -> dict[str, str]:
    env = os.environ.copy()
    env["OLLAMA_API_BASE"] = ollama_url
    env["AIDER_ANALYTICS"] = "false"
    env["AIDER_CHECK_UPDATE"] = "false"
    env["AIDER_SHOW_RELEASE_NOTES"] = "false"
    env["UV_OFFLINE"] = "1"
    env["NPM_CONFIG_OFFLINE"] = "true"
    env["PNPM_CONFIG_OFFLINE"] = "true"
    return env


def run_aider(
    repo: Path,
    *,
    task: str,
    model: str,
    ollama_url: str,
    aider_command: str,
    test_command: str | None,
    pass_number: int,
    pass_total: int,
) -> int:
    print(f"[aider {pass_number}/{pass_total}] implementing with {model}...")
    command = [
        aider_command,
        "--model",
        f"ollama_chat/{model}",
        "--message",
        task,
        "--yes",
        "--no-auto-commits",
        "--no-dirty-commits",
        "--no-attribute-author",
        "--no-attribute-committer",
        "--no-attribute-co-authored-by",
        "--no-auto-lint",
        "--no-pretty",
        "--no-stream",
        "--no-check-update",
        "--no-show-release-notes",
        "--analytics-disable",
    ]
    if test_command:
        print(f"Aider validation command: {test_command}")
        command.extend(["--test-cmd", test_command, "--auto-test"])

    process = subprocess.Popen(
        command,
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=aider_environment(ollama_url),
        shell=False,
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip()
        if line:
            print(f"AIDER: {line}")
    return_code = process.wait()
    print(f"Aider pass {pass_number} finished with exit code {return_code}.")
    return return_code


def ollama_chat(url: str, model: str, messages: list[dict[str, str]], timeout: int = 600) -> dict[str, Any]:
    payload = json.dumps(
        {"model": model, "messages": messages, "stream": False, "format": "json"}
    ).encode("utf-8")
    req = request.Request(
        f"{url.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AgentError(f"Ollama request failed for model {model}: {exc}") from exc

    content = body.get("message", {}).get("content", "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AgentError(f"Reviewer returned invalid JSON: {content[:2000]}") from exc
    if not isinstance(parsed, dict):
        raise AgentError("Reviewer response must be a JSON object.")
    return parsed


def run_reviewer(repo: Path, task: str, base: str, ollama_url: str, model: str) -> dict[str, Any]:
    install_dir = Path(__file__).resolve().parent
    system_prompt = (install_dir / "reviewer_prompt.md").read_text(encoding="utf-8")
    diff = working_diff(repo, base)
    if not diff.strip():
        return {
            "approved": False,
            "summary": "No changes were found to review.",
            "issues": ["No repository diff exists."],
        }

    review = ollama_chat(
        ollama_url,
        model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Requested task:\n{task}\n\nProposed diff:\n{diff}"},
        ],
    )
    issues = review.get("issues", [])
    if not isinstance(issues, list):
        issues = [str(issues)]
    normalized = [str(issue).strip() for issue in issues if str(issue).strip()]
    approved = review.get("approved") is True
    summary = str(review.get("summary", "")).strip() or (
        "Approved." if approved else "Reviewer found a blocking issue."
    )
    if not approved and not normalized:
        normalized = ["Reviewer rejected the change without an actionable issue."]
    return {"approved": approved, "summary": summary, "issues": normalized}


def default_commit_message(task: str) -> str:
    summary = re.sub(r"\s+", " ", task).strip().rstrip(".")
    if len(summary) > 65:
        summary = summary[:65].rstrip()
    if not summary:
        summary = "implement requested change"
    return f"feat: {summary[0].lower() + summary[1:]}"


def cleanup_no_change_branch(repo: Path, base: str, branch: str) -> None:
    git(repo, "switch", base)
    git(repo, "branch", "-D", branch)


def command_develop(args: argparse.Namespace) -> int:
    repo = ensure_git_repo(Path(args.repo))
    ensure_clean(repo)
    branch = create_work_branch(repo, args.base, args.branch, args.task)
    print(f"Working branch: {branch}")

    test_command = args.test_cmd or detected_test_command(repo)
    return_code = run_aider(
        repo,
        task=args.task,
        model=args.model,
        ollama_url=args.ollama_url,
        aider_command=args.aider_command,
        test_command=test_command,
        pass_number=1,
        pass_total=args.review_passes,
    )
    if return_code != 0:
        print(f"Aider implementation failed with exit code {return_code}.", file=sys.stderr)
        return return_code

    if not working_diff(repo, args.base).strip():
        cleanup_no_change_branch(repo, args.base, branch)
        print("NO_CHANGES_REQUIRED: Aider completed without repository changes.")
        return 0

    review: dict[str, Any] | None = None
    for review_pass in range(1, args.review_passes + 1):
        print(f"[review {review_pass}/{args.review_passes}] asking {args.reviewer_model}...")
        review = run_reviewer(repo, args.task, args.base, args.ollama_url, args.reviewer_model)
        print(f"Reviewer summary: {review['summary']}")
        if review["approved"]:
            print("Review approved")
            break

        if review_pass < args.review_passes:
            feedback = "\n".join(f"- {issue}" for issue in review["issues"])
            revision_task = (
                f"Original task:\n{args.task}\n\n"
                "An independent reviewer found these blocking issues. Fix only these issues, "
                f"then run the relevant validation:\n{feedback}"
            )
            return_code = run_aider(
                repo,
                task=revision_task,
                model=args.model,
                ollama_url=args.ollama_url,
                aider_command=args.aider_command,
                test_command=test_command,
                pass_number=review_pass + 1,
                pass_total=args.review_passes,
            )
            if return_code != 0:
                print(f"Aider revision failed with exit code {return_code}.", file=sys.stderr)
                return return_code

    if not review or not review["approved"]:
        print("Reviewer did not approve the change.", file=sys.stderr)
        return 2

    if args.no_commit:
        print("Review approved. Changes left uncommitted because --no-commit was supplied.")
        return 0

    git(repo, "add", "-A")
    commit_message = args.commit_message or default_commit_message(args.task)
    commit = git(repo, "commit", "-m", commit_message, check=False)
    if commit.returncode != 0:
        print(commit.stdout)
        print(commit.stderr, file=sys.stderr)
        return commit.returncode or 1

    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    print(commit.stdout.strip())
    print(f"AI development complete: {branch} @ {sha}")
    print("Nothing was pushed. Gina or a human may now push/create a PR.")
    return 0


def command_review(args: argparse.Namespace) -> int:
    repo = ensure_git_repo(Path(args.repo))
    review = run_reviewer(repo, args.task, args.base, args.ollama_url, args.reviewer_model)
    print(json.dumps(review, indent=2, ensure_ascii=False))
    return 0 if review["approved"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Aider + Gemma against an InstaBounty repository.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    develop = subparsers.add_parser("develop", help="Implement, review and optionally commit a task.")
    develop.add_argument("--repo", required=True)
    develop.add_argument("--task", required=True)
    develop.add_argument("--base", default="main")
    develop.add_argument("--branch")
    develop.add_argument("--model", default=DEFAULT_DEVELOPER_MODEL)
    develop.add_argument("--reviewer-model", default=DEFAULT_REVIEWER_MODEL)
    develop.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    develop.add_argument("--aider-command", default=DEFAULT_AIDER_COMMAND)
    develop.add_argument("--test-cmd")
    develop.add_argument("--review-passes", type=int, default=2)
    develop.add_argument("--commit-message")
    develop.add_argument("--no-commit", action="store_true")
    develop.set_defaults(func=command_develop)

    review = subparsers.add_parser("review", help="Review the current repository diff only.")
    review.add_argument("--repo", required=True)
    review.add_argument("--task", default="Review the current changes.")
    review.add_argument("--base", default="main")
    review.add_argument("--reviewer-model", default=DEFAULT_REVIEWER_MODEL)
    review.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    review.set_defaults(func=command_review)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (AgentError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
