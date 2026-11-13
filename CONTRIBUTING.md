# Contributing

Thanks for your interest. This accelerator shows a pattern: code computes and pins scores, an agent explains them, and a verifier checks the answer.

## Ground rules

- Only claims backed by evidence in `docs/evidence/` belong in docs. Label synthetic or illustrative material.
- `scorer.py` is the only source of numbers. Agents and verifiers must not change scores.
- Follow the [approved cloud architecture](docs/azure-mapping.md): scoring and mandatory answer verification/fallback belong in the Azure Function App, while the receipt and Fabric IQ agents remain separate. Historical experiment harnesses are not alternative product architectures.
- Never commit secrets, private tenant or subscription configuration, or real customer data. `deploy/parameters.json`, `fabric/parameters.json` and the real approval roles file stay untracked; edit copies of their templates.
- Do not claim fewer fires, safer crews or faster work. The evidence does not support it.

## Workflow

1. Fork and create a branch.
2. `pip install -e .[dev]` then `python -m pytest -q`. All tests must pass.
3. If you change the policy, scorer or data, regenerate the receipt hashes and update the locked-hash test.
4. Open a pull request describing what changed and which evidence file backs it.

Use the [testing runbook](docs/testing.md) and [experiment reproduction guide](docs/experiment.md).
Write new experiment outputs separately from committed evidence. Preserve the frozen
v9 article and its consumed figures unless a change is explicitly authorised.
