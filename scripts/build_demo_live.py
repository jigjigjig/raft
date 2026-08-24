#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json

from raft.config import get_settings
from raft.db import Database, utc_now
from raft.otari import OtariClient
from raft.redaction import contains_pii, verify_literal_quote
from raft.schemas import TraceLabel


PROMPT_VERSION = "trace-label-v1"


async def label_rows(*, gate_only: bool, gate_approved: bool) -> None:
    settings = get_settings()
    if settings.raft_otari_mode != "live":
        raise SystemExit("Set RAFT_OTARI_MODE=live. The accepted dataset must come from real Otari labelling.")
    if not gate_only and not gate_approved:
        raise SystemExit("Refusing to label downstream rows without --gate-approved after a manual read of 20 labelled traces.")
    database = Database(settings.raft_database_path)
    database.initialize()
    client = OtariClient(settings, database)
    limit_clause = "LIMIT 40" if gate_only else ""
    rows = database.fetch_all(
        f"""
        SELECT t.*, GROUP_CONCAT(s.content_redacted, '\n') AS conversation
        FROM traces t JOIN spans s ON s.trace_id=t.id
        WHERE t.id NOT IN (SELECT trace_id FROM trace_label_provenance)
        GROUP BY t.id ORDER BY t.id {limit_clause}
        """
    )
    if gate_only and not rows:
        print("The first 40 rows already have live label provenance. Run scripts/review_labels.py.")
        return
    for index, row in enumerate(rows, 1):
        payload = {"trace_id": row["id"], "conversation_redacted": row["conversation"]}
        result, label = await client.complete(
            "trace_labeler",
            [
                {
                    "role": "system",
                    "content": (
                        "Label one redacted trace. Use unclear when capture is insufficient. "
                        "verbatim_quote must be copied literally from conversation_redacted."
                    ),
                },
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_schema=TraceLabel,
        )
        assert label is not None
        quote_valid = verify_literal_quote(label.verbatim_quote, row["conversation"])
        pii_free = not contains_pii(label.user_request + " " + label.what_happened + " " + label.verbatim_quote)
        if not quote_valid:
            raise RuntimeError(f"{row['id']} returned a non-literal quote; stop before downstream work")
        if not pii_free:
            raise RuntimeError(f"{row['id']} returned configured PII; stop before downstream work")
        database.execute(
            """
            UPDATE traces SET outcome=?,failure_mode=?,summary=?,user_request=?,what_happened=?,
                              verbatim_quote=?,model=?,provider=?,guardrail_status=? WHERE id=?
            """,
            (
                "resolved" if label.intent_satisfied == "yes" else "unsatisfied" if label.intent_satisfied == "no" else "unclear",
                label.failure_mode,
                label.what_happened,
                label.user_request,
                label.what_happened,
                label.verbatim_quote,
                result.model or client.role("trace_labeler").primary,
                result.provider or "unknown",
                "passed",
                row["id"],
            ),
        )
        database.execute(
            """
            INSERT INTO trace_label_provenance(
              trace_id,request_id,model_id,provider,prompt_version,schema_valid,quote_valid,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                row["id"],
                result.request_id,
                result.model or client.role("trace_labeler").primary,
                result.provider,
                PROMPT_VERSION,
                1,
                1,
                utc_now(),
            ),
        )
        print(f"{index}/{len(rows)} {row['id']} request_id={result.request_id}", flush=True)
    if gate_only:
        print("STOP: run scripts/review_labels.py and manually read 20 rows. Do not build downstream data until the gate passes.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Real Otari labelling pass for the synthetic demo corpus.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("label-gate", help="Label only the first 40 traces, then stop.")
    rest = subparsers.add_parser("label-rest", help="Label remaining traces only after the human gate.")
    rest.add_argument("--gate-approved", action="store_true", help="Confirm the documented 20-trace gate passed.")
    args = parser.parse_args()
    asyncio.run(label_rows(gate_only=args.command == "label-gate", gate_approved=getattr(args, "gate_approved", False)))


if __name__ == "__main__":
    main()
