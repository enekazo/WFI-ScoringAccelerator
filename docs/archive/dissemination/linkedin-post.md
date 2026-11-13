# LinkedIn post draft

Target: publish within 3 days (panel is end of November). Replace the link placeholder before posting.

---

Would you let a smart intern calculate your wildfire risk scores?

I tested the AI version of that question. I asked large language models to score 50 synthetic land parcels from a written rubric. They were off by 26 to 58 points on a 100-point scale. In up to 46% of answers they also got the data's origin wrong, for example calling generated weather "observed".

So I built the supervisor version. Code does the math and stamps each score on a tamper-evident receipt. The AI only explains it, like an intern briefing the boss. A checker compares every number and label in the explanation with the receipt, and blocks anything that does not match.

Three honest findings:

1. Safe, yes. Smarter, no. Delivered answers had zero score error, but that is largely by design: most unchecked answers were blocked. The AI adds convenience, not accuracy.
2. Is it more useful than a plain template? Roughly a coin flip. A blind judge preferred the AI 53% of the time and the template 42%. That is not statistically significant.
3. Measure your measurements. About 41% of the original "blocks" were rate-limit errors, not the checker catching mistakes.

Limits: 50 synthetic parcels, cost not measured, and the checker still blocks some good answers.

If you put an LLM over numbers people must defend later, this pattern makes it safe and measurable.

Code, charts and evidence: [REPO URL]

#ResponsibleAI #MicrosoftFoundry #MicrosoftFabric #AIGovernance #Wildfire
