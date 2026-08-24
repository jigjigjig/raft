# Manual 20-trace labelling gate

Do not build competition claims on the live labeller until this gate passes.

Run:

```sh
python scripts/build_demo_live.py label-gate
python scripts/review_labels.py --limit 20
```

Acceptance:

- At least 18/20 rows have acceptable satisfaction, failure mode, request summary, and outcome.
- 20/20 quotes are literal substrings of the redacted trace.
- 20/20 rows are free of configured PII.
- No row remains schema-invalid after one repair attempt.

| Trace ID | Satisfaction | Failure mode | Summary | Quote literal | PII clear | Verdict | Notes |
|---|---|---|---|---|---|---|---|
| pending |  |  |  |  |  |  | Live Otari gate has not run. |

Only after this table passes, continue with:

```sh
python scripts/build_demo_live.py label-rest --gate-approved
```
