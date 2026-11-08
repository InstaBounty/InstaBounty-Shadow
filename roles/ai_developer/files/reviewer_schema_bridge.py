#!/usr/bin/env python3
"""Structured-output compatibility bridge for the local AI reviewer."""

from __future__ import annotations

import json
import re
from urllib import error, request


MAX_REPOSITORY_CONTEXT = 2_000
REVIEWER_CONTEXT_WINDOW = 32_768
EVIDENCE_START = "BEGIN IMPLEMENTATION DIFF"
EVIDENCE_END = "END IMPLEMENTATION DIFF"

_REQUIREMENT_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "satisfied": {"type": "boolean"},
        "evidence": {"type": "string", "minLength": 1},
    },
    "required": ["id", "satisfied", "evidence"],
}


def _contract_ids(messages: list[dict[str, str]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for message in messages:
        content = message.get("content", "")
        for match in re.finditer(r"(?m)^\s*([RF]\d+):\s+", content):
            requirement_id = match.group(1)
            if requirement_id not in seen:
                seen.add(requirement_id)
                ids.append(requirement_id)
    return ids


def _review_schema(contract_ids: list[str]) -> dict:
    item = json.loads(json.dumps(_REQUIREMENT_ITEM))
    if contract_ids:
        item["properties"]["id"] = {"type": "string", "enum": contract_ids}
    requirements: dict = {"type": "array", "items": item}
    if contract_ids:
        requirements["minItems"] = len(contract_ids)
        requirements["maxItems"] = len(contract_ids)

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "approved": {"type": "boolean"},
            "summary": {"type": "string"},
            "requirements": requirements,
            "issues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["approved", "summary", "requirements", "issues"],
    }


def _prepare_reviewer_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Split reviewer metadata and implementation diff into separate messages.

    The implementation diff is deliberately the final user message so it cannot be
    accidentally hidden behind repository context or prompt rewriting.
    """
    prepared: list[dict[str, str]] = []
    evidence_message: dict[str, str] | None = None

    for message in messages:
        copied = dict(message)
        content = copied.get("content", "")
        repository_marker = "Repository file context:\n"
        diff_marker = "\n\nProposed diff:\n"

        if message.get("role") == "user" and repository_marker in content and diff_marker in content:
            before_repository, remainder = content.split(repository_marker, 1)
            repository_context, diff = remainder.rsplit(diff_marker, 1)
            if not diff.strip():
                raise ValueError("Reviewer payload contains an empty proposed diff.")

            capped_context = repository_context[:MAX_REPOSITORY_CONTEXT]
            copied["content"] = (
                before_repository
                + "Repository file context (supplemental, capped):\n"
                + capped_context
            )
            evidence_message = {
                "role": "user",
                "content": (
                    "The following block is the authoritative implementation evidence for this review. "
                    "Evaluate every requirement against it together with the validation evidence above.\n\n"
                    f"{EVIDENCE_START}\n{diff}\n{EVIDENCE_END}\n\n"
                    "Do not claim that the diff is missing when the BEGIN/END markers are present."
                ),
            }
            print(
                "Reviewer evidence package: "
                f"diff_chars={len(diff)}, repository_context_chars={len(capped_context)}."
            )

        prepared.append(copied)

    if evidence_message is None:
        raise ValueError("Reviewer payload did not contain the expected proposed-diff evidence block.")

    prepared.append(evidence_message)
    return prepared


def _validate_contract(review: dict, contract_ids: list[str]) -> None:
    if not contract_ids:
        return
    requirements = review.get("requirements")
    if not isinstance(requirements, list):
        raise ValueError("Reviewer response did not contain a requirements array.")
    returned_ids = [item.get("id") for item in requirements if isinstance(item, dict)]
    if returned_ids != contract_ids:
        raise ValueError(
            "Reviewer requirement checklist mismatch. "
            f"Expected IDs in order: {', '.join(contract_ids)}; "
            f"returned: {', '.join(str(value) for value in returned_ids) or '<none>'}."
        )
    for item in requirements:
        evidence = item.get("evidence") if isinstance(item, dict) else None
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(f"Reviewer requirement {item.get('id', '<unknown>')} has empty evidence.")


def _request_review(
    url: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: int,
    contract_ids: list[str],
) -> dict:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": _review_schema(contract_ids),
            "options": {"temperature": 0, "num_ctx": REVIEWER_CONTEXT_WINDOW},
        }
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
        raise RuntimeError(f"Ollama structured reviewer request failed for model {model}: {exc}") from exc

    content = body.get("message", {}).get("content", "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Reviewer returned invalid JSON: {content[:2000]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Reviewer response must be a JSON object.")
    _validate_contract(parsed, contract_ids)
    return parsed


def apply(namespace: dict, argv: list[str]) -> None:
    agent_error = namespace["AgentError"]

    def structured_ollama_chat(
        url: str,
        model: str,
        messages: list[dict[str, str]],
        timeout: int = 600,
    ) -> dict:
        try:
            prepared_messages = _prepare_reviewer_messages(messages)
        except ValueError as exc:
            raise agent_error(str(exc)) from exc

        contract_ids = _contract_ids(prepared_messages)
        try:
            return _request_review(url, model, prepared_messages, timeout, contract_ids)
        except ValueError as first_error:
            first_detail = str(first_error)[:2000]
            exact_ids = ", ".join(contract_ids) if contract_ids else "the IDs supplied in the checklist"
            retry_messages = [
                *prepared_messages,
                {
                    "role": "user",
                    "content": (
                        "Your previous reviewer response did not satisfy the required structured review contract. "
                        f"Return exactly one requirements entry for every contract ID, in this exact order: {exact_ids}. "
                        f"The implementation diff is in the final message between {EVIDENCE_START} and {EVIDENCE_END}. "
                        "Use that block as the primary implementation evidence. Each entry must contain id, satisfied, "
                        "and non-empty concrete evidence. Return only one JSON object matching the supplied schema."
                    ),
                },
            ]
            print("Reviewer contract retry: structured review was incomplete; retrying reviewer once.")
            try:
                return _request_review(url, model, retry_messages, timeout, contract_ids)
            except ValueError as second_error:
                raise agent_error(
                    "Reviewer failed the structured requirement contract after reviewer-only retry. "
                    f"First failure: {first_detail} Final failure: {str(second_error)[:2000]}"
                ) from second_error
            except RuntimeError as exc:
                raise agent_error(str(exc)) from exc
        except RuntimeError as exc:
            raise agent_error(str(exc)) from exc

    namespace["ollama_chat"] = structured_ollama_chat
    run_reviewer = namespace.get("run_reviewer")
    if not callable(run_reviewer):
        raise agent_error("Reviewer schema bridge could not locate run_reviewer.")
    run_reviewer.__globals__["ollama_chat"] = structured_ollama_chat

    if run_reviewer.__globals__.get("ollama_chat") is not structured_ollama_chat:
        raise agent_error("Reviewer schema bridge failed to patch run_reviewer globals.")

    print("Reviewer schema bridge active: explicit diff evidence transport installed.")


def _self_test() -> None:
    messages = [
        {
            "role": "user",
            "content": (
                "Checklist:\nR1: first\nR2: second\nF1: feedback\n\n"
                "Repository file context:\nfile-a\nfile-b\n\n"
                "Proposed diff:\ndiff --git a/a b/a\n+change"
            ),
        }
    ]
    prepared = _prepare_reviewer_messages(messages)
    ids = _contract_ids(prepared)
    assert ids == ["R1", "R2", "F1"]
    assert len(prepared) == 2
    assert "Proposed diff:" not in prepared[0]["content"]
    assert EVIDENCE_START in prepared[-1]["content"]
    assert "+change" in prepared[-1]["content"]
    assert EVIDENCE_END in prepared[-1]["content"]

    schema = _review_schema(ids)
    requirements = schema["properties"]["requirements"]
    assert requirements["minItems"] == 3
    assert requirements["maxItems"] == 3
    assert requirements["items"]["properties"]["id"]["enum"] == ids

    try:
        _prepare_reviewer_messages(
            [{"role": "user", "content": "Repository file context:\nx\n\nProposed diff:\n"}]
        )
    except ValueError:
        pass
    else:
        raise AssertionError("empty proposed diff should fail reviewer payload validation")


if __name__ == "__main__":
    _self_test()
    print("reviewer schema bridge self-test passed")
