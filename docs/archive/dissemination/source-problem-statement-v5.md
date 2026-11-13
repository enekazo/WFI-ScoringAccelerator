# Operationalizing Agentic Wildfire Prevention: A Governance-First Blueprint for EU Forestry (V5)

## Executive summary

EU forestry operators need to move from AI **analysis** to AI-supported **operations** without losing legal accountability, execution authority, and decision traceability. The core challenge is not whether models can produce useful recommendations, but whether those recommendations can be converted into safe, auditable, and authorized interventions at parcel and time-window level.

This paper defines an RMF-aligned governance architecture for wildfire prevention, implemented through Microsoft solution patterns and supported by repository-backed implementation evidence. The architecture separates:

1. Recommendation (what agents and analytics propose)
2. Authority (who can approve and commit operational action)
3. Evidence (what must be current, scoped, and traceable to proceed)

The guiding question is: how can EU forestry organizations operationalize agentic wildfire prevention with Microsoft-native patterns so planning is faster while execution remains policy-compliant, evidence-bound, and human-accountable?

## 1. Why this problem is hard in practice (the operational gaps)

<table style="width:100%; border-collapse:separate; border-spacing:12px;">
  <tr>
    <td style="width:50%; vertical-align:top; border:1px solid #d0d7de; border-radius:14px; padding:14px;">
      <strong>Gap 1 — Regulatory translation gap</strong><br><br>
      <strong>What it is:</strong> Policy obligations are written as legal and governance expectations, while field teams need concrete decision rules per intervention scope and timing.<br>
      <strong>Why it matters:</strong> If legal intent is not translated into executable controls, operations become inconsistent and hard to defend [1][3].
    </td>
    <td style="width:50%; vertical-align:top; border:1px solid #d0d7de; border-radius:14px; padding:14px;">
      <strong>Gap 2 — Evidence and reporting gap</strong><br><br>
      <strong>What it is:</strong> Organizations must defend not only outcomes, but the evidence path behind each decision.<br>
      <strong>Why it matters:</strong> Sustainability and AI governance expectations require traceability, validity windows, and replayable decisions [2][6].
    </td>
  </tr>
  <tr>
    <td style="width:50%; vertical-align:top; border:1px solid #d0d7de; border-radius:14px; padding:14px;">
      <strong>Gap 3 — Signal-to-action gap</strong><br><br>
      <strong>What it is:</strong> Near-real-time fire signals are available, but progression from signal to approved intervention remains slow and fragmented.<br>
      <strong>Why it matters:</strong> Monitoring velocity often exceeds approval velocity, increasing both delay and risk exposure [4][5].
    </td>
    <td style="width:50%; vertical-align:top; border:1px solid #d0d7de; border-radius:14px; padding:14px;">
      <strong>Gap 4 — Governance-control gap</strong><br><br>
      <strong>What it is:</strong> Recommendation quality and execution authority are often mixed in day-to-day workflows.<br>
      <strong>Why it matters:</strong> Safe operations require explicit gates between recommendation, approval, and execution, with accountable owners at each stage [6][7].
    </td>
  </tr>
  <tr>
    <td style="width:50%; vertical-align:top; border:1px solid #d0d7de; border-radius:14px; padding:14px;">
      <strong>Gap 5 — Accountability replay gap</strong><br><br>
      <strong>What it is:</strong> Post-event scrutiny requires reconstruction of who decided, when, using which evidence and policy state.<br>
      <strong>Why it matters:</strong> Without replay, model quality alone is insufficient for operational defensibility [2][6].
    </td>
    <td style="width:50%;"></td>
  </tr>
</table>

## 2. RMF-aligned governance architecture (three operational control planes)

To operationalize AI safely in EU forestry, the architecture should be anchored in the NIST AI RMF lifecycle (Govern, Map, Measure, Manage) and implemented as three operational control planes: recommendation, authority, and evidence [6]. The key is not adding one "smart agent," but ensuring every proposed intervention passes through controlled transitions before execution.

In practice, the control-plane model operationalizes RMF outcomes in a form that delivery teams can implement: recommendation services accelerate analysis, authority services enforce accountable decisions, and evidence services enforce traceability, freshness, and replayability. This also aligns with EU risk-based obligations for human oversight, logging, and robustness [7].

![Governance-first framework overview](../../dissemination/governance-first-agentic-forestry-diagram.svg)
*Figure 1. Governance-first framework overview (recommendation -> authority -> evidence).*

### 2.1 Recommendation layer (what agents can do)

The recommendation layer should produce decision-support artifacts: wildfire risk prioritization, prevention scheduling options, ignition alert candidates, and compliance-evidence drafts. In this blueprint, these capabilities are best implemented with Azure AI Foundry agents/responses, optionally supported by semantic Q&A for exploration.
The critical rule is that recommendations remain advisory. A model can propose what to do next and why, but it does not decide whether an intervention is operationally authorized.

### 2.2 Authority layer (who decides and who is accountable)

The authority layer is the operational control plane. It should remain in a transactional system (here, PostgreSQL/PostGIS) with explicit workflow states for review, approval, budget, allocation, and execution readiness.
This layer enforces decision rights and prevents governance drift. Even when AI confidence is high, progression still depends on designated authority checks. This is where organizations keep accountability with human owners and policy controls, rather than with model output.

### 2.3 Evidence layer (what must be true before progression)

The evidence layer determines whether a recommendation can move forward. Evidence must be scope-correct, current for the decision window, traceable to source, and reviewable after the fact.
For this, the practical pattern is Fabric OneLake medallion products (Bronze/Silver/Gold) for governed evidence and analytics, with read models for reporting and replay. If evidence is missing, stale, conflicting, or not bound to the exact decision context, progression is blocked (fail-closed).

### 2.4 Microsoft implementation pattern for this use case

| Layer | Primary services | Practical role |
| --- | --- | --- |
| Recommendation | Azure AI Foundry Agents/Responses (optional semantic Q&A) | Generate risk, scheduling, alert, and evidence-support recommendations |
| Authority | PostgreSQL/PostGIS + API control workflows | Enforce approval, budget, allocation, and execution decision rights |
| Evidence | Fabric OneLake (Bronze/Silver/Gold), governed read models | Provide traceable, replayable evidence and reporting context |

Identity boundary: Microsoft Entra ID should separate user and service identities and enforce role-scoped access across recommendation, authority, and evidence paths.

M365 agent positioning: Microsoft 365 agents are useful as a productivity channel (briefings, notifications, task follow-up), but should not be the primary operational decision engine for wildfire interventions.

![Governance flow map with Microsoft tooling](../../dissemination/governance-ms-tools-pastel-map.svg)
*Figure 2. Governance flow map with Microsoft tooling mapped to each control layer.*

A wildfire-prevention action is safe to operationalize only when all three control planes converge on the same decision instance: the same intervention, the same location scope, and the same execution window.

### 2.5 Decision lifecycle and fail-closed checkpoints

In operational use, the governance model should run as a repeatable lifecycle rather than an ad hoc sequence of checks. A practical lifecycle is:

1. **Recommend:** the agent proposes intervention options with explicit assumptions, confidence, and freshness context.
2. **Screen for policy eligibility:** operations outside approved policy boundaries are rejected before deeper processing.
3. **Check authority readiness:** required decision owners and workflow prerequisites are verified before evidence validation.
4. **Validate evidence:** scope, validity window, provenance, and version alignment are checked against the exact decision instance.
5. **Authorize or block:** only decisions that pass both authority and evidence gates progress to executable status.
6. **Execute and log:** execution proceeds with immutable metadata linking recommendation, approval path, and evidence set.
7. **Replay and review:** post-event review can reconstruct full causality from input context through final action.

The lifecycle must fail closed at each checkpoint. If scope IDs do not match, if evidence is expired, if authority is missing, or if policy status is unresolved, the state remains blocked and cannot be promoted by model output alone. This is the main control that prevents unsafe acceleration: recommendations can be generated quickly, but only governance-complete recommendations can be operationalized.

Designing the lifecycle explicitly also improves organizational learning. Blocked decisions become a structured signal (e.g., recurring evidence freshness failures, recurring authority bottlenecks) that can be measured and improved without weakening guardrails.

The need to separate recommendation from authority became concrete when implementing the platform's prevention-approval workflow. The requirement was straightforward: let a user approve or reject proposed jobs without turning a planning interaction into permission to dispatch crews or start field work.

The chosen design placed a thin application layer over the existing transaction engine. Separate planner and approver identities preserved decision ownership, while the engine enforced the conditions for progression. The interface could request a decision; it could not bypass the controls behind it.

This raised a more consequential question: **what happens when the evidence changes after a reservation has been made?** The controlled scenario below captures the design boundary: retain the reservation for traceability, but reject subsequent work approval when its supporting assessment is invalidated. Historical workflow state must not become continuing permission to act.

The trade-off is deliberate: progression pauses for reassessment rather than continuing on outdated evidence. The implementation demonstrates this boundary through simulated decisions and bookings; it does not establish real-world dispatch or field outcomes.

#### Evidence vignette: when conditions change, the decision must change

A prevention plan can be reasonable when prepared and no longer safe to progress when work is due. The reference implementation tests this boundary in a controlled scenario: an intervention receives a reservation after initial prerequisites are met, then a simulated weather-change event invalidates the supporting assessment for that same release and job.

At that point, the recommendation still exists, the earlier reviews remain in history, and the reservation remains recorded. But the operations approval attempt is rejected because the assessment is no longer valid for progression. The history is preserved without being treated as continuing permission to act.

The reservation remains recorded, but its projected status requires reassessment and explicitly indicates that starting work is not authorized. The architectural distinction is between preserving a past decision and permitting the next action.

This scenario illustrates the downstream governance boundary required by the three-plane framework. The proposal remains advisory, the authority service rejects progression, and the invalidated assessment prevents earlier workflow state from being mistaken for current eligibility.

Implementation evidence in the reference implementation:

- [`tests/unit/test_prevention_transactions.py` (`test_approval_and_booking_invalidate_on_actual_control_change`, `Harness.ready`)](https://github.com/enekazo/wildfire-intelligence-platformv2/blob/421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb/tests/unit/test_prevention_transactions.py#L121-L269)
- [`src/wildfire_intelligence/prevention/transactions.py` (`ApprovalService`, `ReservationService`, `booking_status`)](https://github.com/enekazo/wildfire-intelligence-platformv2/blob/421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb/src/wildfire_intelligence/prevention/transactions.py#L253-L389)
- [`docs/guides/prevention-transactions.md`](https://github.com/enekazo/wildfire-intelligence-platformv2/blob/421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb/docs/guides/prevention-transactions.md)

This scenario uses simulated identities, controlled inputs, and an in-memory transactional store. It is an executable governance specification, not evidence of live weather integration, field incidents, or measured wildfire-risk reduction. The test defines expected behavior; include a dated execution receipt (command and output) before asserting test-pass evidence in publication material.

### 2.6 Monday-start operating pack (from implementation evidence to repeatable practice)

To make this usable beyond one engagement, the architecture should be implemented as a standard decision packet and gate policy that any forestry platform team can adopt in the first sprint. The key synthesis is that prompt quality alone cannot enforce operational decision boundaries; explicit decision-state controls are also required.

**Decision packet (minimum required fields before any execution-eligible state):**

1. Intervention ID, parcel scope, and execution window.
2. Recommendation metadata (`as_of`, confidence, assumptions, model/release ID).
3. Authority chain (required approvers, current approval state, budget state).
4. Evidence set (source IDs, version/hash, freshness window, policy checks).
5. Outcome state (`blocked`, `eligible`, `authorized`, `executed`) with timestamped transitions.

**Non-negotiable gate policy (fail-closed):**

- **No scope match, no progression:** recommendation, authority, and evidence must match the same intervention scope and time window.
- **No freshness, no progression:** expired or superseded evidence cannot be treated as current.
- **No authority, no progression:** high confidence does not bypass human approval states.
- **No replay trail, no progression:** if immutable decision metadata is missing, execution state is invalid.

These controls are grounded in implementation evidence from the reference repository patterns (scope/version binding, control-state enforcement, and replay metadata) and aligned to external governance expectations [6][7][8]. This is the paper's original operating claim: **agentic wildfire prevention should be treated as a governed decision-state machine, not an autonomous execution pipeline**.

### 2.7 Anti-patterns and design decisions for architects

The following design decisions were selected to address the following operational failure modes:

| Common anti-pattern | Resulting risk | Required design decision |
|---|---|---|
| Single API returns both recommendation and execution status | De facto auto-approval path | Separate recommendation APIs from authority transaction APIs |
| Flexible identifier handling across services | Scope drift across parcel/time/release | Enforce exact scope/version equality at every gate |
| "Best effort" fallback on source/policy failure | Silent downgrade to weak or stale evidence | Fail closed and return explicit blocked state |
| Mutable audit fields overwritten by latest state | Inability to defend post-event decisions | Preserve immutable transition log and evidence digests |
| Agent-generated identity context | Broken accountability and role confusion | Bind identity only from trusted Entra-authenticated context |

For architecture teams, this table is intended as a direct implementation checklist: if any anti-pattern remains in design review, the system is not ready for operational wildfire prevention.

## 3. Agent roles and guardrails

The governance model becomes operational only when role boundaries, technical controls, and approval gates are implemented as one system. This section moves from role design (what each agent is allowed to do), to mandatory controls (what every production path must enforce), to failure patterns (what breaks when controls are weakened).

Agents should accelerate analysis and preparation, not replace accountability. The matrix below defines role boundaries and corresponding implementation controls.

| Agent | Primary contribution | Guardrail requirement | How to implement | Human gate |
|---|---|---|---|---|
| Risk and fuel assessment | Produces parcel/stand risk context and prioritization | Must remain recommendation-only and expose uncertainty/freshness | Provide read-only scoring endpoints with `as_of`, confidence, and source metadata; expose no operational write path | Planning owner validates use in cycle |
| Prevention scheduling | Proposes job sequencing and package options | Cannot commit execution state or bypass blocked controls | Enforce state transitions in authority API; reject progression unless approval + budget + evidence gates pass | Operations + budget owner approve progression |
| Ignition detection/alert | Generates alert candidates from monitoring inputs | Alert is not dispatch order; uncertain detections stay unresolved | Separate alert creation from dispatch command; require explicit human confirmation before escalation | Incident/operations lead confirms escalation |
| Compliance evidence | Assembles evidence pack for decision review | Cannot grant legal clearance; stale/conflicting evidence blocks progression | Validate evidence scope/time/version before acceptance; fail-closed on missing or invalid controls | Compliance owner confirms sufficiency |
| Reporting/insights | Produces trend and exposure views | Cannot alter operational state; must preserve lineage | Serve read models only; persist provenance fields (source, version, timestamp) in all outputs | Governance review approves use for decisions |

### 3.1 Required guardrails for any operational agent path

The role matrix above must be enforced by a shared technical baseline across all agent paths. For operational wildfire prevention, every production path should implement the following controls:

1. **Bounded tool surface:** expose only approved read/recommendation functions to the model; keep operational writes behind non-LLM endpoints.
2. **Strict contract validation:** validate schema, types, size limits, and allowed fields before any source read; reject on first violation.
3. **Scope and version pinning:** require exact dataset/scenario/replay/release identifiers on every request and response.
4. **Explicit authority separation:** keep authority flags non-authoritative in recommendation outputs; approvals are separate transactions.
5. **Trusted identity path:** derive user/service identity from host authentication, never from model-provided arguments.
6. **Fail-closed error handling:** surface source and policy failures as blocking states; prohibit fallback to stale or weaker data.
7. **State and expiry integrity:** preserve invalidated/expired/superseded statuses verbatim; never coerce to current eligibility.
8. **Replay-by-design logging:** record scope, decision owner, evidence/version identifiers, timestamps, and outcome for forensic replay.

These controls keep agents in a recommendation boundary while placing authority and execution in explicit, auditable governance gates, consistent with AI risk-management and secure-AI deployment expectations [6][7][8][9].

### 3.2 Common failure modes and mitigation controls

When the baseline controls in Section 3.1 are implemented inconsistently, the same governance failures reappear across sectors. In wildfire operations, where decision windows are time-sensitive and signal conditions can change rapidly, these breakdowns carry higher operational impact [4][5]. The table below maps each failure mode to its control response and supporting governance guidance [6][7][8][9][10].

| Failure mode | Typical cause | Required mitigation | Credible external basis |
|---|---|---|---|
| Scope drift | Recommendation, evidence, and approval reference different parcel/time/release identifiers | Enforce strict scope/version equality checks on every transition | Traceability, documentation, and controlled lifecycle context are foundational controls [6][7][8] |
| Stale evidence accepted as current | Missing or weak validity-window checks | Apply hard freshness gates and explicit expiry states; no inferred "still valid" status | Time-sensitive wildfire signals require explicit recency handling and uncertainty control [4][5] |
| Recommendation treated as approval | UX or API path conflates proposal and authorization | Separate recommendation APIs from authority transaction endpoints | Human oversight and accountable deployment roles are explicit governance expectations [6][7][8] |
| Silent fallback to weaker data | Error-handling optimized for continuity rather than integrity | Fail closed on source failure; prohibit auto-downgrade to stale/unauthenticated paths | Secure-by-default operation and resilient failure behavior are recommended in secure-AI and risk-management guidance [6][8][9] |
| Overconfident alert escalation | Alert confidence misused as operational authority | Require human confirmation and policy gate before dispatch-level actions | Risk-proportionate safeguards and high-impact action controls are core in risk-based AI governance [7][8][9] |
| Incomplete replay trail | Missing provenance fields or mutable logs | Persist immutable decision metadata (who, what scope, which evidence, when, outcome) | Auditability and defensible reporting expectations in EU sustainability/compliance contexts [1][2] |

These controls are baseline requirements, not enhancements. In regulated operational environments, blocking uncertain execution is usually safer than authorizing action on weak, stale, or untraceable evidence [6][7][8].

## 4. Conclusion and recommendations

The central conclusion of this paper is that operational AI safety in EU forestry depends less on model sophistication and more on governance architecture. Wildfire prevention can benefit materially from agentic systems, but only when recommendation, authority, and evidence are separated and recomposed through explicit control gates.

A governance-first approach does not slow innovation; it makes innovation operationally durable. It allows organizations to accelerate planning and analytical throughput while preserving legal defensibility, role accountability, and post-event auditability. In practice, this means the strongest system is not the one that automates the most actions, but the one that most reliably distinguishes between:

- what the model proposes,
- what policy permits,
- what evidence proves,
- and what accountable roles authorize.

For EU forestry operators, the recommended next steps are:

1. **Adopt a formal decision-state model** for wildfire-prevention workflows, with explicit blocked/eligible/authorized states.
2. **Implement scope-and-version locking** across recommendation, authority, and evidence paths so all decisions reference the same intervention instance.
3. **Enforce fail-closed defaults** for missing, stale, conflicting, or unverifiable evidence.
4. **Separate recommendation services from authority transactions** at API and role level.
5. **Institutionalize replay-by-design** so post-event governance review is a routine capability, not an emergency exercise.

This blueprint should be read as an operating standard for safe adoption: use agentic AI to improve speed and quality of planning, but never bypass the authority and evidence controls that make operational action trustworthy. When applied consistently, this model provides a practical path from AI-enabled analysis to AI-supported, defensible execution in high-risk forestry contexts.

## 5. Project reference mapping (informative)

Reference repository: [`enekazo/wildfire-intelligence-platformv2`](https://github.com/enekazo/wildfire-intelligence-platformv2) at reviewed commit `421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb`.

The repository provides implementation evidence for feasibility of this Microsoft-aligned governance architecture; it is not the normative source of policy requirements. The mapping below links paper claims to implementation artifacts:

| Claim in this paper | Implementation evidence (reviewed commit) |
| --- | --- |
| Scope/version-bound progression controls | [`tests/unit/test_prevention_transactions.py`](https://github.com/enekazo/wildfire-intelligence-platformv2/blob/421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb/tests/unit/test_prevention_transactions.py), [`src/wildfire_intelligence/prevention/transactions.py`](https://github.com/enekazo/wildfire-intelligence-platformv2/blob/421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb/src/wildfire_intelligence/prevention/transactions.py) |
| Fail-closed handling when assessments are invalidated | [`test_approval_and_booking_invalidate_on_actual_control_change`](https://github.com/enekazo/wildfire-intelligence-platformv2/blob/421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb/tests/unit/test_prevention_transactions.py#L255-L269), `TransactionConflict` checks in the same module |
| Replay-ready status semantics and reassessment projection | [`booking_status` implementation](https://github.com/enekazo/wildfire-intelligence-platformv2/blob/421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb/src/wildfire_intelligence/prevention/transactions.py#L377-L389), [`docs/guides/prevention-transactions.md`](https://github.com/enekazo/wildfire-intelligence-platformv2/blob/421ba0f3eb0d7bd02d46dfc49f3222492f9bcefb/docs/guides/prevention-transactions.md) |

## References

[1] European Commission — Regulation on deforestation-free products (EUDR):  
https://environment.ec.europa.eu/topics/forests/deforestation/regulation-deforestation-free-products_en

[2] European Commission — Corporate sustainability reporting (CSRD) policy page:  
https://finance.ec.europa.eu/financial-markets/company-reporting-and-auditing/company-reporting/corporate-sustainability-reporting_en

[3] European Commission — Nature Restoration Regulation context (EU 2024/1991):  
https://environment.ec.europa.eu/topics/nature-and-biodiversity/nature-restoration-regulation_en

[4] Copernicus — EFFIS (European Forest Fire Information System):  
https://forest-fire.emergency.copernicus.eu/

[5] NASA — FIRMS (Fire Information for Resource Management System):  
https://firms.modaps.eosdis.nasa.gov/

[6] NIST — AI Risk Management Framework (AI RMF):  
https://www.nist.gov/itl/ai-risk-management-framework

[7] European Commission — EU AI Act risk-based framework summary:  
https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai

[8] UK NCSC — Guidelines for secure AI system development:  
https://www.ncsc.gov.uk/collection/guidelines-secure-ai-system-development

[9] OWASP GenAI Security Project — OWASP Top 10 for LLM Applications 2026:  
https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/

[10] Stanford HAI — AI Index Report 2025 (Responsible AI ecosystem section):  
https://hai.stanford.edu/ai-index/2025-ai-index-report
