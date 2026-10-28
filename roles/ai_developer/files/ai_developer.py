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
import tomllib
from typing import Any
from urllib import error, request

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_DEVELOPER_MODEL = "qwen3-coder:30b"
DEFAULT_REVIEWER_MODEL = "gemma3:4b"
DEFAULT_AIDER_COMMAND = "aider"
PROTECTED_BRANCHES = {"main", "master"}
MAX_DIFF = 120_000
MAX_VALIDATION_FEEDBACK = 16_000
COVERAGE_FILE = "coverage.json"
HOUSEKEEPING_FILES = {
    ".gitignore",
    ".aider.conf.yml",
    ".aider.model.metadata.json",
    ".aider.model.settings.yml",
    ".editorconfig",
}


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
            "Repository has uncommitted changes. Commit/stash them before starting the AI developer.\n" + status
        )


def slugify(value: str, limit: int = 36) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:limit].rstrip("-") or "change"


def create_work_branch(repo: Path, base: str, branch: str | None, task: str) -> tuple[str, bool]:
    if not base:
        raise AgentError("A base branch is required.")
    git(repo, "show-ref", "--verify", f"refs/heads/{base}")

    branch_name = branch or f"ai/{slugify(task)}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if branch_name.lower() in PROTECTED_BRANCHES:
        raise AgentError(f"Refusing to use protected branch: {branch_name}")
    if not (branch_name.startswith("ai/") or branch_name.startswith("feature/")):
        raise AgentError("AI work branches must start with 'ai/' or 'feature/'.")

    exists = git(repo, "show-ref", "--verify", f"refs/heads/{branch_name}", check=False).returncode == 0
    if exists:
        if branch is None:
            raise AgentError(f"Generated AI branch already exists: {branch_name}")
        git(repo, "switch", branch_name)
        return branch_name, True

    git(repo, "switch", base)
    git(repo, "switch", "-c", branch_name)
    return branch_name, False


def working_diff(repo: Path, base: str) -> str:
    git(repo, "add", "-N", "--", ".", check=False)
    staged = git(repo, "diff", "--cached", check=False).stdout
    unstaged = git(repo, "diff", check=False).stdout
    diff = staged + unstaged
    committed = git(repo, "diff", f"{base}...HEAD", check=False).stdout
    if committed.strip():
        diff = committed + diff
    if len(diff) > MAX_DIFF:
        return diff[:MAX_DIFF] + f"\n... diff truncated at {MAX_DIFF} characters ..."
    return diff


def changed_files(repo: Path, base: str) -> list[str]:
    git(repo, "add", "-N", "--", ".", check=False)
    paths: set[str] = set()
    for args in (
        ("diff", "--name-only", f"{base}...HEAD"),
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
    ):
        output = git(repo, *args, check=False).stdout
        paths.update(line.strip().replace("\\", "/") for line in output.splitlines() if line.strip())
    return sorted(paths)


def repository_file_context(repo: Path, limit: int = 400) -> str:
    files = git(repo, "ls-files", check=False).stdout.splitlines()
    visible = files[:limit]
    suffix = "\n... repository file list truncated ..." if len(files) > limit else ""
    return "\n".join(visible) + suffix


def housekeeping_only_issue(task: str, files: list[str]) -> str | None:
    if not files:
        return "No repository changes were produced for a task that requires implementation work."
    task_lower = task.lower()
    if "gitignore" in task_lower or ".gitignore" in task_lower or "aider" in task_lower:
        return None
    meaningful = [
        path
        for path in files
        if path not in HOUSEKEEPING_FILES
        and not path.startswith(".aider")
        and not path.startswith(".vscode/")
        and not path.startswith(".idea/")
    ]
    if meaningful:
        return None
    return (
        "The proposed diff contains only tooling/housekeeping files "
        f"({', '.join(files)}) and does not implement the requested production/test behaviour."
    )


def _task_explicitly_requests_component_tests(task: str) -> bool:
    lowered = task.lower()
    return "component test" in lowered or "component tests" in lowered


def _is_component_test_file(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    return (
        "/component/" in f"/{normalized}/"
        or normalized.startswith("tests/component/")
        or normalized.startswith("features/")
        or normalized.endswith(".feature")
    )


def _is_unit_test_file(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    if _is_component_test_file(normalized):
        return False
    return (
        normalized.startswith("tests/unit/")
        or normalized.startswith("test/")
        or normalized.startswith("tests/")
        or "/test_" in normalized
        or normalized.endswith("_test.py")
    )


def component_test_policy_issue(task: str, files: list[str]) -> str | None:
    if _task_explicitly_requests_component_tests(task):
        return None
    component_changes = [path for path in files if _is_component_test_file(path)]
    if not component_changes:
        return None
    return (
        "Component-test files were changed even though the original task did not explicitly request "
        f"component tests: {', '.join(component_changes)}."
    )


def explicit_unit_test_issue(task: str, files: list[str]) -> str | None:
    lowered = task.lower()
    requires_unit_tests = bool(
        re.search(r"\b(add|create|write|update)\b.{0,30}\bunit tests?\b", lowered)
        or re.search(r"\bunit tests?\b.{0,30}\b(verif|cover|assert)", lowered)
        or re.search(r"\btests?\s+verifying\b", lowered)
    )
    if not requires_unit_tests:
        return None
    if any(_is_unit_test_file(path) for path in files):
        return None
    return "The task explicitly requires unit tests, but no unit-test file is present in the proposed changes."


def python_package_name(repo: Path) -> str | None:
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    module_name = data.get("tool", {}).get("uv", {}).get("build-backend", {}).get("module-name")
    if isinstance(module_name, str) and module_name.strip():
        return module_name.strip().split(".", 1)[0]
    project_name = data.get("project", {}).get("name")
    if isinstance(project_name, str) and project_name.strip():
        return project_name.strip().replace("-", "_")
    return None


def detected_test_command(repo: Path) -> str | None:
    package = python_package_name(repo)
    if package is not None:
        test_root = "tests/unit" if (repo / "tests" / "unit").is_dir() else "tests"
        return (
            f"uv run --with pytest-cov pytest {test_root} "
            f"--cov={package} --cov-report=term-missing "
            f"--cov-report=json:{COVERAGE_FILE} --cov-fail-under=100"
        )
    if (repo / "pnpm-lock.yaml").is_file():
        return "pnpm test"
    if (repo / "package.json").is_file():
        return "npm test"
    if (repo / "foundry.toml").is_file():
        return "forge test"
    return None


def production_python_files(files: list[str]) -> list[str]:
    return [path for path in files if path.startswith("src/") and path.endswith(".py")]


def coverage_summary(repo: Path) -> tuple[float | None, str]:
    coverage_path = repo / COVERAGE_FILE
    if not coverage_path.is_file():
        return None, f"{COVERAGE_FILE} was not produced."
    try:
        data = json.loads(coverage_path.read_text(encoding="utf-8"))
        percentage = float(data["totals"]["percent_covered"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None, f"{COVERAGE_FILE} could not be parsed."
    return percentage, f"Unit-test code coverage: {percentage:.2f}%."


def coverage_issue(repo: Path, files: list[str]) -> str | None:
    production_files = production_python_files(files)
    if not production_files:
        return None
    percentage, detail = coverage_summary(repo)
    if percentage is None or percentage < 100.0:
        return (
            f"Python production files changed ({', '.join(production_files)}), but {detail} "
            "100% unit-test coverage is required."
        )
    return None


def deterministic_quality_issues(repo: Path, task: str, files: list[str]) -> list[str]:
    issues = [
        housekeeping_only_issue(task, files),
        component_test_policy_issue(task, files),
        explicit_unit_test_issue(task, files),
        coverage_issue(repo, files),
    ]
    return [issue for issue in issues if issue]


def coverage_context(repo: Path) -> str:
    _, detail = coverage_summary(repo)
    return detail


def _prepare_validation_artifacts(repo: Path, test_command: str | None) -> None:
    if test_command and COVERAGE_FILE in test_command:
        (repo / COVERAGE_FILE).unlink(missing_ok=True)


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
        "--yes-always",
        "--no-auto-commits",
        "--no-dirty-commits",
        "--no-attribute-author",
        "--no-attribute-committer",
        "--no-attribute-co-authored-by",
        "--no-auto-lint",
        "--no-suggest-shell-commands",
        "--disable-playwright",
        "--no-detect-urls",
        "--no-add-gitignore-files",
        "--env-file",
        "NUL",
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


def run_validation(repo: Path, test_command: str | None, ollama_url: str) -> subprocess.CompletedProcess[str] | None:
    if not test_command:
        return None
    _prepare_validation_artifacts(repo, test_command)
    print(f"Independent validation command: {test_command}")
    result = subprocess.run(
        test_command,
        cwd=str(repo),
        text=True,
        capture_output=True,
        timeout=600,
        shell=True,
        env=aider_environment(ollama_url),
    )
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if combined:
        for line in combined.splitlines():
            print(f"VALIDATION: {line}")
    if result.returncode == 0:
        print("Independent validation passed.")
    else:
        print(f"Independent validation failed with exit code {result.returncode}.")
    return result


def _validation_feedback(result: subprocess.CompletedProcess[str]) -> str:
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if len(combined) > MAX_VALIDATION_FEEDBACK:
        combined = combined[-MAX_VALIDATION_FEEDBACK:]
        combined = f"... validation output truncated ...\n{combined}"
    return combined or f"Validation failed with exit code {result.returncode}."


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


def _explicit_requirement_count(task: str) -> int:
    numbered = re.findall(r"(?m)^\s*\d+[.)]\s+\S", task)
    if numbered:
        return len(numbered)
    bullets = re.findall(r"(?m)^\s*[-*]\s+\S", task)
    return len(bullets)


def _normalize_review_requirements(
    review: dict[str, Any], task: str
) -> tuple[list[dict[str, str | bool]], list[str]]:
    raw_requirements = review.get("requirements")
    if not isinstance(raw_requirements, list) or not raw_requirements:
        return [], ["Reviewer did not provide requirement-by-requirement verification."]

    requirements: list[dict[str, str | bool]] = []
    issues: list[str] = []
    expected = _explicit_requirement_count(task)
    if expected and len(raw_requirements) < expected:
        issues.append(
            f"Reviewer verified only {len(raw_requirements)} requirement(s), but the task contains "
            f"at least {expected} explicit requirement(s)."
        )

    for index, item in enumerate(raw_requirements, start=1):
        if not isinstance(item, dict):
            issues.append(f"Reviewer requirement {index} is malformed.")
            continue
        requirement = str(item.get("requirement", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        satisfied = item.get("satisfied") is True
        if not requirement:
            issues.append(f"Reviewer requirement {index} has no requirement text.")
        if not evidence:
            issues.append(f"Reviewer requirement {index} has no evidence.")
        if not satisfied:
            issues.append(
                f"Requirement not satisfied: {requirement or f'requirement {index}'}. "
                f"{evidence or 'No supporting evidence was supplied.'}"
            )
        requirements.append({"requirement": requirement, "satisfied": satisfied, "evidence": evidence})
    return requirements, issues


def run_reviewer(repo: Path, task: str, base: str, ollama_url: str, model: str) -> dict[str, Any]:
    install_dir = Path(__file__).resolve().parent
    system_prompt = (install_dir / "reviewer_prompt.md").read_text(encoding="utf-8")
    diff = working_diff(repo, base)
    files = changed_files(repo, base)

    deterministic_issues = deterministic_quality_issues(repo, task, files)
    if deterministic_issues:
        return {
            "approved": False,
            "summary": "Deterministic quality gate rejected the proposed changes.",
            "requirements": [],
            "issues": deterministic_issues,
        }

    review = ollama_chat(
        ollama_url,
        model,
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Requested task:\n{task}\n\n"
                    f"Changed files:\n" + "\n".join(files) + "\n\n"
                    f"Coverage evidence:\n{coverage_context(repo)}\n\n"
                    f"Repository file context:\n{repository_file_context(repo)}\n\n"
                    f"Proposed diff:\n{diff}"
                ),
            },
        ],
    )

    raw_issues = review.get("issues", [])
    if not isinstance(raw_issues, list):
        raw_issues = [str(raw_issues)]
    normalized_issues = [str(issue).strip() for issue in raw_issues if str(issue).strip()]

    requirements, requirement_issues = _normalize_review_requirements(review, task)
    normalized_issues.extend(requirement_issues)

    approved = review.get("approved") is True and not normalized_issues
    summary = str(review.get("summary", "")).strip() or (
        "Approved." if approved else "Reviewer found a blocking issue."
    )
    if not approved and not normalized_issues:
        normalized_issues = ["Reviewer rejected the change without an actionable issue."]
    return {
        "approved": approved,
        "summary": summary,
        "requirements": requirements,
        "issues": normalized_issues,
    }


def default_commit_message(task: str) -> str:
    summary = re.sub(r"\s+", " ", task).strip().rstrip(".")
    if len(summary) > 65:
        summary = summary[:65].rstrip()
    if not summary:
        summary = "implement requested change"
    return f"feat: {summary[0].lower() + summary[1:]}"


def cleanup_no_change_branch(repo: Path, base: str, branch: str, reused: bool) -> None:
    if reused:
        return
    git(repo, "switch", base)
    git(repo, "branch", "-D", branch)


def _development_task(task: str, repo: Path) -> str:
    policy = [
        "Quality requirements:",
        "- Satisfy every explicit requirement in the original task exactly.",
        "- Production-code changes must be covered by unit tests.",
        "- For Python production-code changes, unit-test coverage must remain at 100%.",
        "- Do not create or modify component tests unless the original task explicitly requests component tests.",
    ]
    if python_package_name(repo) is not None:
        policy.append(
            "- Use the configured unit-test coverage command and fix every test failure and uncovered line before finishing."
        )
    return f"{task}\n\n" + "\n".join(policy)


def _revision_task(task: str, reason: str) -> str:
    return (
        f"Original task:\n{task}\n\n"
        "The independent validation/requirement gate found blocking issues. Fix them while still "
        "satisfying every original requirement. Maintain 100% unit-test coverage for Python production "
        "code and do not create or modify component tests unless the original task explicitly requested them.\n\n"
        f"Blocking evidence:\n{reason}"
    )


def command_develop(args: argparse.Namespace) -> int:
    repo = ensure_git_repo(Path(args.repo))
    ensure_clean(repo)
    branch, reused = create_work_branch(repo, args.base, args.branch, args.task)
    print(f"Working branch: {branch}")
    if reused:
        print(f"Reusing existing AI branch: {branch}")

    test_command = args.test_cmd or detected_test_command(repo)
    max_passes = max(1, args.review_passes)
    pass_number = 1
    current_task = _development_task(args.task, repo)
    review: dict[str, Any] | None = None

    while pass_number <= max_passes:
        return_code = run_aider(
            repo,
            task=current_task,
            model=args.model,
            ollama_url=args.ollama_url,
            aider_command=args.aider_command,
            test_command=test_command,
            pass_number=pass_number,
            pass_total=max_passes,
        )
        if return_code != 0:
            print(f"Aider pass {pass_number} failed with exit code {return_code}.", file=sys.stderr)
            return return_code

        if not working_diff(repo, args.base).strip():
            cleanup_no_change_branch(repo, args.base, branch, reused)
            print("NO_CHANGES_REQUIRED: Aider completed without repository changes.")
            return 0

        validation = run_validation(repo, test_command, args.ollama_url)
        if validation is not None and validation.returncode != 0:
            if pass_number >= max_passes:
                print("Independent validation did not pass after the final Aider pass.", file=sys.stderr)
                return validation.returncode or 1
            current_task = _revision_task(args.task, _validation_feedback(validation))
            pass_number += 1
            continue

        print(f"[review {pass_number}/{max_passes}] asking {args.reviewer_model}...")
        review = run_reviewer(repo, args.task, args.base, args.ollama_url, args.reviewer_model)
        print(f"Reviewer summary: {review['summary']}")
        for issue in review.get("issues", []):
            print(f"Reviewer issue: {issue}")

        if review["approved"]:
            print("Review approved")
            break

        if pass_number >= max_passes:
            print("Reviewer did not approve the change.", file=sys.stderr)
            return 2

        feedback = "\n".join(f"- {issue}" for issue in review.get("issues", []))
        current_task = _revision_task(args.task, feedback or review["summary"])
        pass_number += 1

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

    develop = subparsers.add_parser("develop", help="Implement, validate, review and optionally commit a task.")
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
