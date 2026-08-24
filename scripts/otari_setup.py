#!/usr/bin/env python3
"""Point Raft at a live Otari and prove the connection before anything depends on it.

Run this first. It discovers which models the deployment can actually serve,
assigns them to Raft's roles, and makes one real call of each kind. A model in a
published catalog is not evidence that a key can reach it, and finding that out
during a demo is the failure this exists to prevent.

    OTARI_KEY=sk-... python scripts/otari_setup.py --base http://localhost:8000
    OTARI_KEY=sk-... python scripts/otari_setup.py --write   # update model-roles.yaml

Nothing is written unless --write is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# Rough preference order for each role's job. Matching is by substring against
# whatever the deployment actually reports, so unknown model names still land
# somewhere sensible rather than failing.
ROLE_PREFERENCES: dict[str, list[str]] = {
    "planner": ["gpt-oss-120b", "120b", "70b", "qwen3-32b", "sonnet", "gpt-4", "large"],
    "aspect_evaluator": ["nano", "mini", "8b", "haiku", "small", "flash", "qwen3-30b"],
    "trace_labeler": ["nano", "mini", "8b", "haiku", "small", "flash", "qwen3-30b"],
    "cluster_namer": ["70b", "qwen3-30b", "sonnet", "gpt-4o", "large"],
    "autopsy_writer": ["70b", "qwen3-30b", "sonnet", "gpt-4o", "large"],
    "span_explainer": ["nano", "mini", "8b", "haiku", "small", "flash"],
}


def pick(models: list[str], preferences: list[str], fallback: str) -> str:
    for want in preferences:
        for model in models:
            if want.casefold() in model.casefold():
                return model
    return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.getenv("OTARI_BASE", "http://localhost:8000"))
    parser.add_argument("--key", default=os.getenv("OTARI_KEY", ""))
    parser.add_argument("--deployment", choices=("standalone", "hosted"), default="standalone")
    parser.add_argument("--write", action="store_true", help="update model-roles.yaml with the discovered models")
    args = parser.parse_args()

    if not args.key:
        print("No key. Pass --key or set OTARI_KEY.", file=sys.stderr)
        return 2

    base = args.base.rstrip("/")
    prefix = "/v1" if args.deployment == "standalone" else "/api/v1"
    headers = {"Authorization": f"Bearer {args.key}"}
    ok = True

    with httpx.Client(timeout=60.0) as client:
        # 1. Which models does this key actually reach?
        print(f"→ GET {base}{prefix}/models")
        response = client.get(f"{base}{prefix}/models", headers=headers)
        if response.status_code >= 400:
            print(f"  FAILED {response.status_code}: {response.text[:300]}", file=sys.stderr)
            return 1
        body = response.json()
        entries = body.get("data") if isinstance(body, dict) else body
        models = sorted({str(item.get("id") or item.get("name")) for item in (entries or []) if isinstance(item, dict)})
        print(f"  {len(models)} model(s) available")
        for model in models[:25]:
            print(f"    {model}")
        if not models:
            print("  No models. Configure a provider credential in Otari first.", file=sys.stderr)
            return 1

        # 2. Assign them to roles.
        roles = yaml.safe_load((ROOT / "model-roles.yaml").read_text())["roles"]
        assignment: dict[str, tuple[str, str]] = {}
        print("\n→ role assignment")
        for name, role in roles.items():
            primary = pick(models, ROLE_PREFERENCES.get(name, []), models[0])
            others = [model for model in models if model != primary]
            fallback = pick(others, ROLE_PREFERENCES.get(name, []), others[0] if others else primary)
            assignment[name] = (primary, fallback)
            print(f"    {name:18} {primary}  (fallback {fallback})")

        # 3. One real completion, because reachability is not capability.
        probe = assignment["aspect_evaluator"][0]
        print(f"\n→ POST {base}/v1/chat/completions  model={probe}")
        chat = client.post(
            f"{base}/v1/chat/completions",
            headers=headers,
            json={
                "model": probe,
                "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                "temperature": 0,
                "max_tokens": 16,
            },
        )
        if chat.status_code >= 400:
            print(f"  FAILED {chat.status_code}: {chat.text[:400]}", file=sys.stderr)
            ok = False
        else:
            content = chat.json()["choices"][0]["message"]["content"]
            print(f"  OK: {str(content).strip()[:60]!r}")

        # 4. Embeddings decide whether paraphrased questions can match at all.
        print(f"\n→ POST {base}/v1/embeddings")
        embed = client.post(
            f"{base}/v1/embeddings",
            headers=headers,
            json={"input": ["my parcel never arrived", "this is going in circles"]},
        )
        if embed.status_code >= 400:
            print(f"  FAILED {embed.status_code}: {embed.text[:300]}", file=sys.stderr)
            print("  Raft will fall back to local TF-IDF vectors.", file=sys.stderr)
            ok = False
        else:
            vectors = embed.json()["data"]
            print(f"  OK: {len(vectors)} vectors of {len(vectors[0]['embedding'])} dimensions")

    if args.write:
        path = ROOT / "model-roles.yaml"
        document = yaml.safe_load(path.read_text())
        for name, (primary, fallback) in assignment.items():
            document["roles"][name]["primary"] = primary
            document["roles"][name]["fallbacks"] = [fallback]
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        print(f"\nwrote {path}")
    else:
        print("\n(dry run — pass --write to update model-roles.yaml)")

    print("\nREADY" if ok else "\nNOT READY — fix the failures above before demoing")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
