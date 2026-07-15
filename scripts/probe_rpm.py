"""Probe SenseNova real RPM limit for deepseek-v4-flash.

Sends sequential minimal chat requests as fast as possible, logs each
attempt's status + timing, and dumps full 429 response headers so we can
reverse-engineer the actual per-minute quota.

Usage:
    uv run python scripts/probe_rpm.py --max-requests 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from openai import OpenAI
from openai import RateLimitError as OAIRateLimitError


def load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    cfg = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-requests", type=int, default=60, help="max requests to send")
    parser.add_argument("--max-seconds", type=int, default=90, help="stop after this many seconds")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    env = load_env()
    base_url = (args.base_url or env.get("LIEPIN_AGENT_API_BASE_URL") or "").rstrip("/")
    api_key = args.api_key or env.get("LIEPIN_AGENT_API_KEY")
    model = args.model or env.get("LIEPIN_AGENT_MODEL_NAME") or "deepseek-v4-flash"
    if not base_url or not api_key:
        print("ERROR: missing base_url or api_key", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)

    print(f"[probe] base_url={base_url} model={model} max_requests={args.max_requests} max_seconds={args.max_seconds}")
    print(f"[probe] sending sequential requests back-to-back...")

    results = []
    started = time.monotonic()
    ok_count = 0
    rate_limited_count = 0
    first_429_at = None
    first_429_headers = None
    first_429_body = None
    for i in range(1, args.max_requests + 1):
        elapsed = time.monotonic() - started
        if elapsed > args.max_seconds:
            print(f"[probe] max_seconds={args.max_seconds} reached at request #{i}, stopping")
            break
        t0 = time.time()
        status = -1
        rl_headers: dict = {}
        body_snippet = ""
        try:
            resp = client.chat.completions.with_raw_response.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                reasoning_effort="none",
                stream=False,
            )
            dt = time.time() - t0
            status = resp.status_code
            rl_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() in (
                    "retry-after",
                    "x-ratelimit-limit",
                    "x-ratelimit-remaining",
                    "x-ratelimit-reset",
                    "x-ratelimit-limit-requests",
                    "x-ratelimit-remaining-requests",
                    "x-ratelimit-reset-requests",
                    "x-ratelimit-limit-tokens",
                    "x-ratelimit-remaining-tokens",
                )
            }
            ok_count += 1
            tag = "OK "
        except OAIRateLimitError as exc:
            dt = time.time() - t0
            status = 429
            rate_limited_count += 1
            if first_429_at is None:
                first_429_at = elapsed
                try:
                    first_429_headers = dict(exc.response.headers) if exc.response is not None else {}
                except Exception:
                    first_429_headers = {}
                try:
                    first_429_body = exc.response.text[:500] if exc.response is not None else str(exc)
                except Exception:
                    first_429_body = str(exc)
            rl_headers = (
                {k: v for k, v in exc.response.headers.items()
                 if k.lower() in (
                     "retry-after",
                     "x-ratelimit-limit",
                     "x-ratelimit-remaining",
                     "x-ratelimit-reset",
                     "x-ratelimit-limit-requests",
                     "x-ratelimit-remaining-requests",
                     "x-ratelimit-reset-requests",
                     "x-ratelimit-limit-tokens",
                     "x-ratelimit-remaining-tokens",
                 )}
                if exc.response is not None else {}
            )
            tag = "429"
            body_snippet = str(exc)[:200]
        except Exception as exc:
            dt = time.time() - t0
            status = -1
            tag = "EXC"
            body_snippet = str(exc)[:200]
        results.append((i, elapsed, status, dt, rl_headers))
        print(f"#{i:3d} t={elapsed:6.2f}s status={tag} dt={dt:5.2f}s rl={rl_headers} {body_snippet}")
        if status == 429 and rate_limited_count == 1 and first_429_body:
            print(f"    429 body: {first_429_body}")

    total_elapsed = time.monotonic() - started
    print()
    print(f"=== SUMMARY ===")
    print(f"total_elapsed={total_elapsed:.2f}s")
    print(f"ok_count={ok_count}")
    print(f"rate_limited_count={rate_limited_count}")
    print(f"first_429_at={first_429_at}")
    if first_429_headers:
        print(f"first_429_headers={json.dumps(first_429_headers, indent=2, ensure_ascii=False)}")
    # estimate RPM from ok requests in first 60s
    ok_in_60 = sum(1 for r in results if r[2] == 200 and r[1] <= 60)
    print(f"ok_in_first_60s={ok_in_60}")
    # window analysis
    print(f"results={json.dumps([(r[0], round(r[1], 2), r[2], round(r[3], 2)) for r in results], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
