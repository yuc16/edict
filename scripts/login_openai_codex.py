#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edict_runtime.codex import ensure_openai_codex_auth, refresh_openai_codex_auth


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Login to OpenAI Codex with ChatGPT Plus/Pro OAuth."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify whether a cached OAuth token is available.",
    )
    args = parser.parse_args()

    try:
        token = (
            ensure_openai_codex_auth(interactive=False)
            if args.check
            else refresh_openai_codex_auth(interactive=True)
        )
    except Exception as exc:
        print(f"OpenAI Codex auth failed: {exc}", file=sys.stderr)
        return 1

    account_id = getattr(token, "account_id", "")
    if args.check:
        print(f"OpenAI Codex token is available for account {account_id}")
    else:
        print(f"Authenticated with OpenAI Codex for account {account_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
