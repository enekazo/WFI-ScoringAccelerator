# Article outline: "Let the AI explain the number, never compute it"

Longer post (blog or LinkedIn article), ~900-1200 words. Publish after the LinkedIn post, before the end-of-November panel.

1. **Hook:** the intern and the supervisor. Would you let an intern compute a number a regulator may review?
2. **The problem:** raw LLM over a scoring rubric. Show the 26-58 point error and 21-46% provenance misstatement; include the score error and provenance charts.
3. **The pattern:** pinned policy, deterministic scorer, hash-pinned receipt, read-only agent, verifier. Use the Mermaid diagram from the README.
4. **What we measured:** 50 synthetic parcels, two models, four arms. Delivered error 0, fault detection 0.99-1.0, reproducible.
5. **What that does and does not mean:** zero error is largely by construction; the agent adds convenience, not accuracy.
6. **Is it useful?** Blind judge versus a plain template: 53% win / 5% tie / 42% loss, not significant. Where it helped (why-incomplete, what-if) and where it lost (next action, compare).
7. **Measurement lesson:** ~41% of original blocks were HTTP 429 errors; some were over-strict wording rules. Show the block-causes chart.
8. **Build it:** Python harness plus Microsoft Foundry agent as the main path; optional Fabric lakehouse.
9. **Limits and when not to use it:** synthetic data, cost unmeasured, verifier false blocks, no stable rules, low stakes.
10. **Call to action:** repo link, try the harness on your own policy and data, run a pilot before relying on it.
