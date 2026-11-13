# Authority plane (reference implementation)

The agent can recommend. People decide. This adds a `proposed -> reviewed -> approved` trail
on Fabric, with the Foundry agent `pinned-score-agent` as the only proposer.

This is a **separate reference workflow**, not a capability exposed by the deployed
receipt or Fabric IQ MCP endpoints. The [approved cloud architecture](azure-mapping.md)
keeps scoring and mandatory receipt-answer verification in the Function App and human
approval in Fabric. The proposal harness below uses the historical caller-executed
function-tool path; it is not the supported cloud receipt-query path or evidence that
the current installer wires cloud proposals. Do not reconfigure a deployed receipt
agent with proposal tools to reproduce this reference workflow.

## How it works

- **Store.** Append-only Delta table `authority_decisions` in the `pinned_scoring_lh` lakehouse
  (written with `deltalake` to OneLake). Each row is hash chained to the previous one
  (`prev`, `hash`), so edits or deleted rows fail `verify`.
- **Agent.** With `create_or_update_agent(..., graph_tools=True, authority=True)` the agent gets one
  extra tool, `propose_action`. It has no tool to review, approve or execute, and its identity
  (`foundry:pinned-score-agent`) can never hold a role.
- **People.** Reviewers and approvers are Entra identities (UPNs) listed in
  `examples/wildfire/roles.json` (copy `roles.example.json`; the real file is gitignored, never commit
  real identities).
- **Separation of duties** (`"separation_of_duties"` in the roles file):
  - `"required"` (default): reviewer and approver must be different named people. Entries are
    `{"upn", "display_name"}`; the file must list a second person who is not also a reviewer, or it
    fails to load.
  - `"single_person"`: the same named person may review and approve. Each review/approve/reject row
    records `mode` (inside the hash), and `status` and `verify` show the configured mode and the
    modes found in the log. **This is not a two-person control**; it only records who acted.
  - In both modes the agent can never review, approve or hold a role. Rows written before the `mode`
    column existed have no mode and still verify.
- **Gates on propose.** Receipt must exist, have `status == scored`, be fresh
  (`--max-age-days`, default 7) and the agent's narration must pass `verify_narration` against the
  receipt. A duplicate proposal id is refused. Rejection needs a reason.
- **Gates on advance.** The person must hold the role for that step, must not have acted on the
  proposal before, and the chain must verify first.

## Reference CLI

These commands target the separate approval reference implementation. Proposal,
review, approval and rejection write Fabric records; they are not read-only tests.
Select your own tenant, Foundry project and Fabric SQL target explicitly and configure
the private roles file before a deliberate reference run. Choose an existing parcel.

```powershell
$env:PYTHONPATH="src;scripts"; $env:PINNED_DATA_SOURCE="fabric"; $env:FABRIC_SQL_DATABASE="pinned_scoring_lh"
python scripts/authority.py propose P127        # historical proposal harness; not the cloud MCP query path
python scripts/authority.py review <proposal_id>
python scripts/authority.py approve <proposal_id>
python scripts/authority.py reject <proposal_id> --note "reason"
python scripts/authority.py status
python scripts/authority.py verify
```

Review, approve and reject use the signed-in user (`az account show`) unless `--as` is given.
Global options (before the subcommand): `--roles`, `--max-age-days`. A blocked action prints
`{"blocked": reason}` and exits with code 2.

## Tests

`tests/test_authority.py` uses an in-memory fake store. **That fake is only in the unit tests;
product code is Fabric-only** (there is no local-file version).
Run: `$env:PYTHONPATH="src;scripts;tests"; python -m pytest -q`.

## Honest scope

This is a reference implementation of **review and approval only**. Budget, allocation, readiness
and execution remain design-only. Approval is a recorded decision, not an action taken on any
system. The log is tamper-evident (hash chain), not tamper-proof: someone with write access to
the table can still rewrite it wholesale. Only one Entra identity exists, so separation of duties
was shown by blocks, not a two-person run. The live approval of P127 (`c501026a3cf853df`) ran in
`single_person` mode (evidence 11-14): the same person reviewed (earlier, before the mode field
existed) and approved. That approval is marked `single_person` and is not a two-person control. The Fabric demo data has a fixed as_of date, so the
live run relaxed the freshness limit. See the [recorded authority evidence](evidence/authority-plane/summary.md).
