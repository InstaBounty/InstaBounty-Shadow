#!/usr/bin/env python3
"""Constrained local development agent for the InstaBounty Shadow workstation."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
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
MAX_FILE_BYTES = 1_000_000
MAX_TOOL_OUTPUT = 30_000
CONSOLE_TOOL_OUTPUT = 4_000
SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", ".next", ".pytest_cache"}
DIRECT_COMMANDS = {"pytest", "ruff", "mypy", "coverage", "solc"}
COMMAND_SUBCOMMANDS = {
    "uv": {"run"},
    "pnpm": {"test", "run", "exec", "lint"},
    "npm": {"test", "run"},
    "forge": {"test", "build", "fmt"},
}
READ_ONLY_GIT_COMMANDS = {"status", "diff", "log", "show", "grep", "ls-files", "rev-parse"}
PROTECTED_BRANCHES = {"main", "master"}


class AgentError(RuntimeError):
    pass


def run_process(args: list[str], cwd: Path, timeout: int = 300, check: bool = False) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["UV_OFFLINE"] = "1"
    environment["NPM_CONFIG_OFFLINE"] = "true"
    environment["PNPM_CONFIG_OFFLINE"] = "true"
    result = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
        env=environment,
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
    return (value[:limit].rstrip("-") or "change")


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

    exists = git(repo, "show-ref", "--verify", f"refs/heads/{branch_name}", check=False)
    if exists.returncode == 0:
        raise AgentError(f"Branch already exists: {branch_name}")
    git(repo, "switch", "-c", branch_name)
    return branch_name


def confined_path(repo: Path, relative: str) -> Path:
    if not relative or relative == ".":
        return repo
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise AgentError(f"Path escapes repository: {relative}") from exc
    if ".git" in candidate.relative_to(repo).parts:
        raise AgentError("Access to .git is not allowed.")
    return candidate


def truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... output truncated at {limit} characters ..."


def list_files(repo: Path, relative: str, pattern: str) -> dict[str, Any]:
    root = confined_path(repo, relative)
    if not root.exists() or not root.is_dir():
        raise AgentError(f"Directory does not exist: {relative}")
    results: list[str] = []
    for current_root, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        current = Path(current_root)
        for name in files:
            path = current / name
            rel = path.relative_to(repo).as_posix()
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
                results.append(rel)
                if len(results) >= 500:
                    return {"files": results, "truncated": True}
    return {"files": results, "truncated": False}


def read_file(repo: Path, relative: str, start_line: int, end_line: int) -> dict[str, Any]:
    path = confined_path(repo, relative)
    if not path.is_file():
        raise AgentError(f"File does not exist: {relative}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise AgentError(f"File is too large to read: {relative}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = max(1, start_line)
    end = min(len(lines), max(start, end_line))
    numbered = "\n".join(f"{index}: {lines[index - 1]}" for index in range(start, end + 1))
    return {"path": relative, "start_line": start, "end_line": end, "content": truncate(numbered)}


def search_text(repo: Path, query: str, relative: str) -> dict[str, Any]:
    if not query:
        raise AgentError("search_text requires a non-empty query.")
    root = confined_path(repo, relative)
    matches: list[dict[str, Any]] = []
    paths = [root] if root.is_file() else [Path(p) for p in list_files(repo, relative, "*")["files"]]
    for entry in paths:
        path = entry if entry.is_absolute() else repo / entry
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for index, line in enumerate(lines, 1):
            if query.lower() in line.lower():
                matches.append({"path": path.relative_to(repo).as_posix(), "line": index, "text": line[:500]})
                if len(matches) >= 200:
                    return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


def write_file(repo: Path, relative: str, content: str) -> dict[str, Any]:
    path = confined_path(repo, relative)
    if path == repo:
        raise AgentError("Cannot write to the repository root as a file.")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise AgentError(f"Refusing to write file larger than {MAX_FILE_BYTES} bytes: {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
    return {"path": relative, "bytes": len(encoded)}


def delete_file(repo: Path, relative: str) -> dict[str, Any]:
    path = confined_path(repo, relative)
    if not path.is_file():
        raise AgentError(f"File does not exist: {relative}")
    path.unlink()
    return {"path": relative, "deleted": True}


def validate_command(args: list[Any]) -> list[str]:
    if not args or not all(isinstance(value, str) and value for value in args):
        raise AgentError("run_command requires a non-empty string array in args.")
    if any("\x00" in value or "\n" in value or "\r" in value for value in args):
        raise AgentError("Command arguments may not contain control characters.")

    command = Path(args[0]).name.lower()
    if command in {"git", "git.exe"}:
        if len(args) < 2 or args[1].lower() not in READ_ONLY_GIT_COMMANDS:
            raise AgentError("Only read-only Git commands are available to the model.")
    elif command in COMMAND_SUBCOMMANDS:
        if len(args) < 2 or args[1].lower() not in COMMAND_SUBCOMMANDS[command]:
            allowed = ", ".join(sorted(COMMAND_SUBCOMMANDS[command]))
            raise AgentError(f"Allowed {command} subcommands: {allowed}")
    elif command not in DIRECT_COMMANDS:
        raise AgentError(f"Command is not allow-listed: {args[0]}")
    return [str(value) for value in args]


def execute_command(repo: Path, args: list[Any]) -> dict[str, Any]:
    command = validate_command(args)
    result = run_process(command, repo, timeout=300, check=False)
    return {
        "args": command,
        "returncode": result.returncode,
        "stdout": truncate(result.stdout),
        "stderr": truncate(result.stderr),
    }


def print_tool_result(action: dict[str, Any], result: dict[str, Any]) -> None:
    if action.get("action") != "run_command":
        return
    args = result.get("args", action.get("args", []))
    print(f"  command: {' '.join(str(value) for value in args)}")
    if "returncode" in result:
        print(f"  exit: {result['returncode']}")
    stdout = str(result.get("stdout", "")).strip()
    stderr = str(result.get("stderr", "")).strip()
    if stdout:
        print("  stdout:")
        print(truncate(stdout, CONSOLE_TOOL_OUTPUT))
    if stderr:
        print("  stderr:")
        print(truncate(stderr, CONSOLE_TOOL_OUTPUT))
    if result.get("error"):
        print(f"  error: {result['error']}")


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
        raise AgentError(f"Model returned invalid JSON: {truncate(content, 2000)}") from exc
    if not isinstance(parsed, dict):
        raise AgentError("Model response must be a JSON object.")
    return parsed


def repository_context(repo: Path) -> str:
    files = git(repo, "ls-files").stdout.splitlines()
    status = git(repo, "status", "--short", "--branch").stdout.strip()
    visible_files = files[:400]
    suffix = "\n... file list truncated ..." if len(files) > len(visible_files) else ""
    return f"Git status:\n{status}\n\nTracked files:\n" + "\n".join(visible_files) + suffix


def dispatch_action(repo: Path, action: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = action.get("action")
    if name == "list_files":
        return "continue", list_files(repo, str(action.get("path", ".")), str(action.get("pattern", "*")))
    if name == "read_file":
        return "continue", read_file(
            repo,
            str(action.get("path", "")),
            int(action.get("start_line", 1)),
            int(action.get("end_line", 250)),
        )
    if name == "search_text":
        return "continue", search_text(repo, str(action.get("query", "")), str(action.get("path", ".")))
    if name == "write_file":
        return "continue", write_file(repo, str(action.get("path", "")), str(action.get("content", "")))
    if name == "delete_file":
        return "continue", delete_file(repo, str(action.get("path", "")))
    if name == "run_command":
        return "continue", execute_command(repo, action.get("args", []))
    if name == "done":
        return "done", {"summary": str(action.get("summary", "Development task complete."))}
    if name == "blocked":
        return "blocked", {"summary": str(action.get("summary", "Development task is blocked."))}
    raise AgentError(f"Unknown model action: {name!r}")


def run_developer(
    repo: Path,
    task: str,
    ollama_url: str,
    model: str,
    max_steps: int,
    feedback: list[str] | None = None,
) -> dict[str, str]:
    install_dir = Path(__file__).resolve().parent
    system_prompt = (install_dir / "developer_prompt.md").read_text(encoding="utf-8")
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Task:\n{task}\n\nRepository context:\n{repository_context(repo)}",
        },
    ]
    for item in feedback or []:
        messages.append({"role": "user", "content": f"Code review feedback to resolve:\n{item}"})

    mutation_seen = False
    for step in range(1, max_steps + 1):
        print(f"[developer {step}/{max_steps}] asking {model}...")
        action = ollama_chat(ollama_url, model, messages)

        if action.get("action") == "execute_command":
            action = {**action, "action": "run_command"}

        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        action_name = str(action.get("action", ""))
        print(f"  action: {action_name or '<missing>'}")
        try:
            status, result = dispatch_action(repo, action)
        except (AgentError, OSError, ValueError, subprocess.SubprocessError) as exc:
            result = {"error": str(exc)}
            status = "continue"
        print_tool_result(action, result)
        messages.append({"role": "user", "content": "Tool result:\n" + json.dumps(result, ensure_ascii=False)})

        if action_name in {"write_file", "delete_file"} and "error" not in result:
            mutation_seen = True

        if (
            action_name == "run_command"
            and mutation_seen
            and result.get("returncode") == 0
        ):
            command = " ".join(str(value) for value in result.get("args", []))
            return {
                "status": "done",
                "summary": f"Repository changes made and validation passed: {command}",
            }

        if status in {"done", "blocked"}:
            return {"status": status, "summary": str(result["summary"])}
    raise AgentError(f"Developer exceeded the maximum of {max_steps} steps.")


def working_diff(repo: Path, base: str) -> str:
    # Intent-to-add makes newly created files visible in `git diff` without staging content.
    git(repo, "add", "-N", "--", ".", check=False)
    staged = git(repo, "diff", "--cached", check=False).stdout
    unstaged = git(repo, "diff", check=False).stdout
    diff = staged + unstaged
    if not diff.strip():
        diff = git(repo, "diff", f"{base}...HEAD", check=False).stdout
    return truncate(diff, 120_000)


def run_reviewer(repo: Path, task: str, base: str, ollama_url: str, model: str) -> dict[str, Any]:
    install_dir = Path(__file__).resolve().parent
    system_prompt = (install_dir / "reviewer_prompt.md").read_text(encoding="utf-8")
    diff = working_diff(repo, base)
    if not diff.strip():
        return {"approved": False, "summary": "No changes were found to review.", "issues": ["No repository diff exists."]}
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Requested task:\n{task}\n\nProposed diff:\n{diff}"},
    ]
    review = ollama_chat(ollama_url, model, messages)
    approved = review.get("approved") is True
    issues = review.get("issues", [])
    if not isinstance(issues, list):
        issues = [str(issues)]
    normalized_issues = [str(issue).strip() for issue in issues if str(issue).strip()]
    summary = str(review.get("summary", "")).strip()
    if not approved and not normalized_issues:
        normalized_issues = [
            "Reviewer rejected the change without an actionable issue. Re-inspect the requested task and proposed diff, then correct any mismatch before requesting review again."
        ]
    if not summary:
        summary = "Approved." if approved else "Reviewer found a blocking issue."
    return {
        "approved": approved,
        "summary": summary,
        "issues": normalized_issues,
    }


def default_commit_message(task: str) -> str:
    summary = re.sub(r"\s+", " ", task).strip().rstrip(".")
    if len(summary) > 65:
        summary = summary[:65].rstrip()
    if not summary:
        summary = "implement requested change"
    return f"feat: {summary[0].lower() + summary[1:]}"


def command_develop(args: argparse.Namespace) -> int:
    repo = ensure_git_repo(Path(args.repo))
    ensure_clean(repo)
    branch = create_work_branch(repo, args.base, args.branch, args.task)
    print(f"Working branch: {branch}")

    developer_result = run_developer(repo, args.task, args.ollama_url, args.model, args.max_steps)
    print(f"Developer summary: {developer_result['summary']}")
    if developer_result["status"] == "blocked":
        print("Developer reported the task as blocked. No review was requested; branch and working tree were left intact for inspection.", file=sys.stderr)
        return 2

    if not working_diff(repo, args.base).strip():
        raise AgentError("Developer reported completion but made no repository changes.")

    review: dict[str, Any] | None = None
    for review_pass in range(1, args.review_passes + 1):
        print(f"[review {review_pass}/{args.review_passes}] asking {args.reviewer_model}...")
        review = run_reviewer(repo, args.task, args.base, args.ollama_url, args.reviewer_model)
        print(json.dumps(review, indent=2, ensure_ascii=False))
        if review["approved"]:
            break
        feedback = "\n".join(f"- {issue}" for issue in review["issues"])
        if review_pass < args.review_passes:
            developer_result = run_developer(
                repo,
                args.task,
                args.ollama_url,
                args.model,
                args.max_steps,
                feedback=[feedback],
            )
            print(f"Developer revision summary: {developer_result['summary']}")
            if developer_result["status"] == "blocked":
                print("Developer revision reported the task as blocked. Branch and working tree were left intact for inspection.", file=sys.stderr)
                return 2

    if not review or not review["approved"]:
        print("Reviewer did not approve the change. The branch and working tree were left intact for inspection.", file=sys.stderr)
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
        print("Commit failed. Changes remain on the AI work branch for inspection.", file=sys.stderr)
        return commit.returncode or 1

    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    print(commit.stdout.strip())
    print(f"AI development complete: {branch} @ {sha}")
    print("Nothing was pushed. Review the commit locally, then push/create a PR when ready.")
    return 0


def command_review(args: argparse.Namespace) -> int:
    repo = ensure_git_repo(Path(args.repo))
    review = run_reviewer(repo, args.task, args.base, args.ollama_url, args.reviewer_model)
    print(json.dumps(review, indent=2, ensure_ascii=False))
    return 0 if review["approved"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local InstaBounty AI developer/reviewer against a Git repository."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    develop = subparsers.add_parser("develop", help="Implement, review, and optionally commit a development task.")
    develop.add_argument("--repo", required=True, help="Path to the Git repository root.")
    develop.add_argument("--task", required=True, help="Development task to implement.")
    develop.add_argument("--base", default="main", help="Local base branch. Default: main")
    develop.add_argument("--branch", help="Work branch. Must start with ai/ or feature/.")
    develop.add_argument("--model", default=DEFAULT_DEVELOPER_MODEL)
    develop.add_argument("--reviewer-model", default=DEFAULT_REVIEWER_MODEL)
    develop.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    develop.add_argument("--max-steps", type=int, default=30)
    develop.add_argument("--review-passes", type=int, default=2)
    develop.add_argument("--commit-message", help="Conventional commit message to use after approval.")
    develop.add_argument("--no-commit", action="store_true", help="Leave approved changes uncommitted.")
    develop.set_defaults(func=command_develop)

    review = subparsers.add_parser("review", help="Review the current repository diff without changing it.")
    review.add_argument("--repo", required=True, help="Path to the Git repository root.")
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
