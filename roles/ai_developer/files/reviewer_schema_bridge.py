#!/usr/bin/env python3
"""Structured-output compatibility bridge for the local AI reviewer."""

from __future__ import annotations

import json
from urllib import error, request


REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "approved": {"type": "boolean"},
        "summary": {"type": "string"},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "satisfied": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["id", "satisfied", "evidence"],
            },
        },
        "issues": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["approved", "summary", "requirements", "issues"],
}


def _request_review(url: str, model: str, messages: list[dict[str, str]], timeout: int) -> dict:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": REVIEW_SCHEMA,
            "options": {"temperature": 0},
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
        raise ValueError(content) from exc
    if not isinstance(parsed, dict):
        raise ValueError(content)
    return parsed


def apply(namespace: dict, argv: list[str]) -> None:
    """Replace the runner's reviewer Ollama call with strict structured output."""
    agent_error = namespace["AgentError"]

    def structured_ollama_chat(
        url: str,
        model: str,
        messages: list[dict[str, str]],
        timeout: int = 600,
    ) -> dict:
        try:
            return _request_review(url, model, messages, timeout)
        except ValueError as first_error:
            malformed = str(first_error)[:2000]
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Your previous response did not satisfy the required JSON response format. "
                        "Return only the JSON object required by the supplied schema. Do not include "
                        "reasoning, Markdown, code fences, introductions, or any text outside the JSON object."
                    ),
                },
            ]
            print("Reviewer format retry: structured JSON response was malformed; retrying reviewer once.")
            try:
                return _request_review(url, model, retry_messages, timeout)
            except ValueError as second_error:
                final_content = str(second_error)[:2000]
                raise agent_error(
                    "Reviewer returned invalid JSON after structured-output retry. "
                    f"First response: {malformed} Final response: {final_content}"
                ) from second_error
            except RuntimeError as exc:
                raise agent_error(str(exc)) from exc
        except RuntimeError as exc:
            raise agent_error(str(exc)) from exc

    namespace["ollama_chat"] = structured_ollama_chat


def _self_test() -> None:
    assert REVIEW_SCHEMA["required"] == ["approved", "summary", "requirements", "issues"]
    item = REVIEW_SCHEMA["properties"]["requirements"]["items"]
    assert item["required"] == ["id", "satisfied", "evidence"]
    assert REVIEW_SCHEMA["additionalProperties"] is False


if __name__ == "__main__":
    _self_test()
    print("reviewer schema bridge self-test passed")
