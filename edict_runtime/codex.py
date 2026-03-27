from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx

try:
    from oauth_cli_kit import get_token, login_oauth_interactive
except ImportError:
    get_token = None
    login_oauth_interactive = None


DEFAULT_CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_ORIGINATOR = "edict-runtime"


@dataclass
class CodexResponse:
    text: str
    finish_reason: str
    usage: dict[str, Any]


def ensure_openai_codex_auth(interactive: bool | None = None, force_login: bool = False):
    if get_token is None:
        raise RuntimeError("oauth-cli-kit is not installed. Run `uv sync` first.")

    token = None
    if not force_login:
        try:
            token = get_token()
        except Exception:
            token = None

    if token and getattr(token, "access", None):
        return token

    if interactive is None:
        interactive = (
            os.getenv("OPENAI_CODEX_AUTO_LOGIN", "1").strip().lower() not in {"0", "false", "no"}
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        )

    if not interactive:
        raise RuntimeError(
            "OpenAI Codex OAuth login required. Run `uv run python scripts/login_openai_codex.py`."
        )
    if login_oauth_interactive is None:
        raise RuntimeError("oauth-cli-kit lacks interactive login support.")

    token = login_oauth_interactive(print_fn=print, prompt_fn=input)
    if token and getattr(token, "access", None):
        return token
    raise RuntimeError("OpenAI Codex OAuth login failed.")


def refresh_openai_codex_auth(interactive: bool = True):
    return ensure_openai_codex_auth(interactive=interactive, force_login=True)


class CodexClient:
    def __init__(self) -> None:
        self.base_url = (os.getenv("OPENAI_CODEX_BASE_URL") or DEFAULT_CODEX_URL).rstrip("/")
        self.originator = os.getenv("OPENAI_CODEX_ORIGINATOR") or DEFAULT_ORIGINATOR
        self.timeout_seconds = float(os.getenv("OPENAI_CODEX_TIMEOUT", "120"))
        self.verify_ssl = os.getenv("OPENAI_CODEX_VERIFY_SSL", "1").strip().lower() not in {
            "0",
            "false",
            "no",
        }

    def complete_text(self, *, model: str, system: str, user: str) -> CodexResponse:
        token = ensure_openai_codex_auth()
        body = {
            "model": _strip_model_prefix(model),
            "store": False,
            "stream": True,
            "instructions": system,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user}],
                }
            ],
            "text": {"verbosity": os.getenv("OPENAI_CODEX_VERBOSITY", "medium")},
            "include": ["reasoning.encrypted_content"],
        }
        headers = _build_headers(
            account_id=getattr(token, "account_id", ""),
            access_token=getattr(token, "access", ""),
            originator=self.originator,
        )
        try:
            text, finish_reason, usage = _request_codex(
                url=self.base_url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
                verify_ssl=self.verify_ssl,
            )
        except RuntimeError as exc:
            if "invalid or expired" not in str(exc):
                raise
            token = refresh_openai_codex_auth(interactive=True)
            headers = _build_headers(
                account_id=getattr(token, "account_id", ""),
                access_token=getattr(token, "access", ""),
                originator=self.originator,
            )
            text, finish_reason, usage = _request_codex(
                url=self.base_url,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
                verify_ssl=self.verify_ssl,
            )
        return CodexResponse(text=text, finish_reason=finish_reason, usage=usage)


def extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if "```json" in candidate:
        candidate = candidate.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in candidate:
        candidate = candidate.split("```", 1)[1].split("```", 1)[0].strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        candidate = candidate[start : end + 1]
    return json.loads(candidate)


def _strip_model_prefix(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[1]
    return model


def _build_headers(account_id: str, access_token: str, originator: str) -> dict[str, str]:
    if not access_token:
        raise RuntimeError("OpenAI Codex OAuth token is empty.")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "OpenAI-Beta": "responses=experimental",
        "originator": originator,
        "User-Agent": "edict-runtime (python)",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _request_codex(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout_seconds: float,
    verify_ssl: bool,
) -> tuple[str, str, dict[str, Any]]:
    try:
        return _request_codex_once(
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            verify_ssl=verify_ssl,
        )
    except Exception as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc) or not verify_ssl:
            raise
        return _request_codex_once(
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            verify_ssl=False,
        )


def _request_codex_once(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout_seconds: float,
    verify_ssl: bool,
) -> tuple[str, str, dict[str, Any]]:
    with httpx.Client(timeout=timeout_seconds, verify=verify_ssl) as client:
        with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code != 200:
                raw = response.read().decode("utf-8", "ignore")
                raise RuntimeError(_friendly_error(response.status_code, raw))
            return _consume_sse(response)


def _consume_sse(response: httpx.Response) -> tuple[str, str, dict[str, Any]]:
    content = ""
    finish_reason = "completed"
    usage: dict[str, Any] = {}

    for event in _iter_sse(response):
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            content += event.get("delta") or ""
        elif event_type == "response.completed":
            payload = event.get("response") or {}
            finish_reason = payload.get("status") or "completed"
            usage = payload.get("usage") or {}
        elif event_type in {"error", "response.failed"}:
            raise RuntimeError("OpenAI Codex response failed.")
    return content, finish_reason, usage


def _iter_sse(response: httpx.Response):
    buffer: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if not buffer:
                continue
            payload: list[str] = []
            for item in buffer:
                if item.startswith("data:"):
                    payload.append(item[5:].strip())
            buffer = []
            raw = "\n".join(payload).strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                yield json.loads(raw)
            except Exception:
                continue
            continue
        buffer.append(line)


def _friendly_error(status_code: int, raw: str) -> str:
    if status_code == 401:
        return "OpenAI Codex OAuth token is invalid or expired. Re-run `uv run python scripts/login_openai_codex.py`."
    if status_code == 403:
        return "OpenAI Codex access denied. Check that the account has ChatGPT Plus/Pro access."
    if status_code == 429:
        return "ChatGPT quota exceeded or rate limited. Try again later."
    return f"HTTP {status_code}: {raw}"
