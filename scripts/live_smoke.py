#!/usr/bin/env python3
"""Run one real, user-authorized smoke request through a running bridge.

This script is intentionally **not** used by CI. It makes one real chat
request through your already-running LMArenaBridge instance, so run it only
on your own machine, with your own Arena account/token configured in the
bridge, and respect Arena's terms and rate limits.

Examples:

    # Terminal 1
    python -m src.main

    # Terminal 2 (bash/zsh)
    export LMAB_API_KEY="<bridge API key from the dashboard>"
    python scripts/live_smoke.py

    # Terminal 2 (PowerShell)
    $env:LMAB_API_KEY = "<bridge API key from the dashboard>"
    python scripts/live_smoke.py --stream

The output contains status codes, model names, timings, and a short response
excerpt. It never prints the bridge API key or request headers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "http://localhost:8000/api/v1"
DEFAULT_PROMPT = "Reply with exactly: bridge smoke ok"


def _redact(text: object, *secrets: str) -> str:
    rendered = str(text or "")
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "[REDACTED]")
    return rendered


def _error_summary(payload: Any, *secrets: str) -> str:
    """Extract a bounded error message from OpenAI/Anthropic-style payloads."""
    message: object = payload
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            bits = [
                str(error.get("type") or "").strip(),
                str(error.get("code") or "").strip(),
                str(error.get("message") or "").strip(),
            ]
            message = " / ".join(bit for bit in bits if bit) or error
        elif error:
            message = error
        elif payload.get("detail"):
            message = payload["detail"]
    return _redact(message, *secrets)[:500]


def _response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]


def _require_success(
    label: str,
    response: httpx.Response,
    *secrets: str,
) -> Any:
    payload = _response_payload(response)
    if response.is_success:
        return payload
    raise RuntimeError(
        f"{label} failed with HTTP {response.status_code}: "
        f"{_error_summary(payload, *secrets)}"
    )


def _extract_content(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        pieces: list[str] = []
        for item in message:
            if isinstance(item, dict):
                pieces.append(str(item.get("text") or item.get("content") or ""))
            else:
                pieces.append(str(item))
        return "".join(pieces)
    return "" if message is None else str(message)


def _check_health(
    client: httpx.Client,
    *,
    allow_degraded: bool,
) -> None:
    started = time.monotonic()
    response = client.get("/health")
    elapsed_ms = (time.monotonic() - started) * 1000
    payload = _response_payload(response)
    checks = payload.get("checks", {}) if isinstance(payload, dict) else {}
    status = payload.get("status", "unknown") if isinstance(payload, dict) else "unknown"
    print(f"[health] HTTP {response.status_code} status={status} in {elapsed_ms:.0f} ms")
    if isinstance(checks, dict) and checks:
        rendered = ", ".join(f"{name}={value}" for name, value in checks.items())
        print(f"[health] checks: {rendered}")
    if response.status_code != 200 and not allow_degraded:
        raise RuntimeError(
            "Bridge is reachable but not ready. Run `python -m src.doctor --check-server`, "
            "fix the reported checks, or pass --allow-degraded to continue anyway."
        )


def _select_model(
    client: httpx.Client,
    requested_model: Optional[str],
    api_key: str,
) -> str:
    started = time.monotonic()
    response = client.get("/models")
    elapsed_ms = (time.monotonic() - started) * 1000
    payload = _require_success("Model list", response, api_key)
    data = payload.get("data") if isinstance(payload, dict) else None
    model_ids = [
        str(item.get("id"))
        for item in (data or [])
        if isinstance(item, dict) and item.get("id")
    ]
    print(f"[models] HTTP {response.status_code} count={len(model_ids)} in {elapsed_ms:.0f} ms")
    if not model_ids:
        raise RuntimeError("The bridge returned no usable models. Refresh models in the dashboard.")

    if requested_model:
        if requested_model not in model_ids:
            preview = ", ".join(model_ids[:10])
            raise RuntimeError(
                f"Requested model {requested_model!r} is not in the bridge model list. "
                f"First available models: {preview}"
            )
        return requested_model

    selected = model_ids[0]
    print(f"[models] selected first model: {selected}")
    return selected


def _run_nonstream_chat(
    client: httpx.Client,
    *,
    model: str,
    prompt: str,
    api_key: str,
) -> str:
    started = time.monotonic()
    response = client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    payload = _require_success("Chat completion", response, api_key)
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Chat completion returned HTTP 200 but no choices.")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = _extract_content(message.get("content"))
    print(f"[chat] HTTP {response.status_code} model={model} in {elapsed_ms:.0f} ms")
    return content


def _run_stream_chat(
    client: httpx.Client,
    *,
    model: str,
    prompt: str,
    api_key: str,
) -> str:
    started = time.monotonic()
    chunks: list[str] = []
    events = 0
    with client.stream(
        "POST",
        "/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        },
    ) as response:
        if not response.is_success:
            response.read()
            _require_success("Streaming chat completion", response, api_key)
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            events += 1
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta") or {}
                if isinstance(delta, dict):
                    chunks.append(_extract_content(delta.get("content")))
    elapsed_ms = (time.monotonic() - started) * 1000
    print(
        f"[chat] HTTP 200 stream model={model} events={events} "
        f"in {elapsed_ms:.0f} ms"
    )
    return "".join(chunks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one live smoke request through an already-running LMArenaBridge."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LMAB_BASE_URL", DEFAULT_BASE_URL),
        help="Bridge API base URL (default: %(default)s or LMAB_BASE_URL)",
    )
    parser.add_argument(
        "--api-key-env",
        default="LMAB_API_KEY",
        help="Environment variable containing the bridge API key (default: LMAB_API_KEY)",
    )
    parser.add_argument("--model", help="Model ID to test; defaults to the first listed model")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Single user prompt to send")
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Per-request timeout in seconds (default: 180)",
    )
    parser.add_argument("--stream", action="store_true", help="Use streaming chat completion")
    parser.add_argument(
        "--skip-chat",
        action="store_true",
        help="Only check health and model listing; do not send an upstream chat request",
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="Continue even if /health does not return HTTP 200",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0:
        print("ERROR: --timeout must be greater than zero.", file=sys.stderr)
        return 2

    api_key = os.environ.get(args.api_key_env, "").strip()
    base_url = args.base_url.rstrip("/")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        print(
            f"[auth] {args.api_key_env} is not set; sending no bridge API key. "
            "This only works during no-key bootstrap or with allow_missing_api_key=true."
        )

    print(f"[target] {base_url}")
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=args.timeout) as client:
            _check_health(client, allow_degraded=args.allow_degraded)
            model = _select_model(client, args.model, api_key)
            if args.skip_chat:
                print("[chat] skipped by --skip-chat; no upstream chat request was sent")
                print("SMOKE OK (health/models only; live chat not verified)")
                return 0

            if args.stream:
                content = _run_stream_chat(
                    client,
                    model=model,
                    prompt=args.prompt,
                    api_key=api_key,
                )
            else:
                content = _run_nonstream_chat(
                    client,
                    model=model,
                    prompt=args.prompt,
                    api_key=api_key,
                )
    except httpx.HTTPError as exc:
        print(
            f"ERROR: Could not reach the bridge at {base_url}: "
            f"{_redact(exc, api_key)}",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {_redact(exc, api_key)}", file=sys.stderr)
        return 1

    excerpt = _redact(" ".join(content.split()), api_key)[:300]
    print(f"[chat] response_chars={len(content)} excerpt={excerpt!r}")
    if not content.strip():
        print("ERROR: Chat returned an empty response.", file=sys.stderr)
        return 1

    print("SMOKE OK (one live bridge chat request completed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
