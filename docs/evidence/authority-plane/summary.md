# Authority plane: live run on Fabric

Publication note: personal data, including actor UPNs, has been pseudonymised
to protect privacy. Deployment identifiers in these copies also use example
replacements. Hash-chain verification results describe the original live
records, preserved privately. Stored hashes have not been recalculated for the
redacted copies, so the actor-redacted rows cannot independently reproduce the
original chain verification.

Run date 2026-11-10. Store: Delta table `authority_decisions` in lakehouse `pinned_scoring_lh`
(workspace `00000000-...`). Agent: `pinned-score-agent` v8, model gpt-4.1, `authority=True`.
Data source: Fabric (`PINNED_DATA_SOURCE=fabric`), parcels P126-P150.

| File | What it shows |
|---|---|
| 01-block-stale-receipt-default-7d.json | Default 7-day freshness gate blocks P127 (receipt as_of 2026-07-15) |
| 02-block-incomplete-receipt-P126.json | Agent reads the incomplete receipt and declines to propose |
| 02b-...-proposal-P127.json | A reworded second proposal for P127 got a new id (ids hash receipt + narration) |
| 03-review-by-signed-in-user.json | Reviewer (signed-in Entra user) advances to `reviewed`; hash chained |
| 04, 05 | Same person cannot review twice or approve: not in the approver role |
| 05b | Same person listed as reviewer AND approver is still blocked (separation of duties) |
| 06 | Non-role user `--as stranger@example.com` blocked |
| 07 | The agent id cannot advance a proposal |
| 08, 09 | Status and hash-chain verification (ok) |
| 10 | Tamper (edit, delete) on a copy of the Fabric rows is detected |

The proposal itself was made by the real Foundry agent (`propose_action` tool, agent answer and
tool calls in the CLI output). The first proposal (c501026a3cf853df) is the one reviewed.

## Honest limits

- Only ONE Entra identity was available. **Separation of duties is demonstrated by the blocks
  (04, 05, 05b), not by a two-person run.** The approve step by a different person has not been run
  live; it is covered by the unit tests. A second approver identity was requested.
- The Fabric receipts have a fixed `as_of` of 2026-07-15, so the live run used
  `--max-age-days 400`. The default 7-day gate correctly blocks (01).
- The tamper demo runs on a copy of the rows read from Fabric. The Fabric table itself is
  append-only and was not modified.
- The roles file used was a local, gitignored `roles.json` (signed-in user as reviewer, a placeholder
  approver).
- The first agent narration attempt was rejected by the verifier (provenance mismatch); the agent
  instructions were tightened (v8) and the error now includes violation details. Nothing was written
  for the rejected attempt.

## Single-person mode (follow-up)

Only one Entra identity exists, so the approve step ran with `"separation_of_duties": "single_person"`.
This is NOT a two-person control.

- 11: P127 proposal `c501026a3cf853df` approved by the signed-in user in single_person mode; row has `mode: single_person`. Its earlier review row predates the mode field (mode empty).
- 12: `status` shows the configured mode and each decision's mode.
- 13: `verify` ok, 4 entries, `modes_recorded_in_log: [single_person]`.
- 14: a `required`-mode roles file with the same person in both lists is refused at load.
- Unit tests cover both modes; the agent can never review or approve in either.
