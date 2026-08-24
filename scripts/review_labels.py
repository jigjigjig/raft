#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from raft.config import get_settings
from raft.db import Database
from raft.redaction import contains_pii, verify_literal_quote


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the 20-trace manual gate worksheet.")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    database = Database(get_settings().raft_database_path)
    rows = database.fetch_all("SELECT * FROM traces ORDER BY id LIMIT ?", (args.limit,))
    print("# Manual labelled-trace gate\n")
    for index, row in enumerate(rows, 1):
        quote_ok = verify_literal_quote(row["verbatim_quote"], row["user_request"])
        pii_ok = not contains_pii(row["user_request"] + " " + row["what_happened"])
        print(f"## {index}. {row['id']}\n")
        print(f"- Request: {row['user_request']}")
        print(f"- Label: outcome={row['outcome']}; failure={row['failure_mode']}")
        print(f"- Summary: {row['summary']}")
        print(f"- Quote literal: {'yes' if quote_ok else 'NO'}")
        print(f"- PII-free: {'yes' if pii_ok else 'NO'}")
        print("- Human judgment acceptable: [ ] yes [ ] no")
        print("- Notes:\n")


if __name__ == "__main__":
    main()
