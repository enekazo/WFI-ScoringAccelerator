# Usefulness and block analysis

The block-rate investigation is in [Scenario 1](reports/scenario-1-locking-the-score.md); the blind-judge usefulness test is in [Scenario 2](reports/scenario-2-ontology.md).

The recorded ontology-agent/template comparison used 20 questions, three judge
repeats and 60 judgements: 35 agent wins, 2 ties and 23 losses. The mean advantage
was +0.57/15, with 95% CI -1.30 to +2.38: **inconclusive**. It was not a comparison
of otherwise identical agents with and without Fabric IQ, nor a human-user study.

Receipt fallback can enforce delivered score consistency without proving that the
agent's advice is more useful. High block rates include infrastructure failures and
wording disagreements, not only model arithmetic errors; retain per-model/run context.
For inputs, judge configuration, repeat counts, safe output locations and the distinction
between direct-model and ontology-agent methods, see [reproduction](experiment.md#experiment-2-fabric-iq-relationship-questions-and-usefulness).
