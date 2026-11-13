# Agent instructions for WFI-ScoringAccelerator

These instructions apply to work in this repository. Follow any more specific instructions in a subdirectory, provided they do not conflict with the user's explicit request.

## Ground every answer in the repository

- Before explaining or changing behavior, inspect the relevant files, configuration, tests, and documentation. Do not rely on memory of earlier versions of the project.
- Cite repository evidence in your response using file paths and, where useful, function names or line numbers. Distinguish what you inspected from what you inferred.
- Do not invent files, APIs, classes, database objects, settings, commands, test results, or product behavior. If something cannot be verified, say so and identify what would be needed to verify it.
- For claims about external services or current product behavior, consult available authoritative documentation when needed; do not present an unverified assumption as fact.
- If the requested file or environment is inaccessible, say so. Do not claim to have inspected or changed it.

## Stay within the requested scope

- Restate the requested outcome briefly, then identify the smallest set of files likely to need changes. Inspect them before editing.
- Make the smallest correct change that meets the request. Follow existing patterns and preserve existing behavior unless the user explicitly asks to change it.
- Do not refactor unrelated code, redesign architecture, rename components, change formatting across unrelated files, add dependencies, or implement adjacent features without explicit authorization.
- Do not modify generated artifacts, secrets, credentials, environment-specific configuration, or data unless the task requires it and the effect is understood.
- If you notice improvements outside scope, list them separately as optional follow-ups. Do not implement them.
- If a consequential ambiguity prevents a safe implementation, ask a focused question rather than guessing. Otherwise make a conservative choice and state the assumption.

## Implement and verify

1. Inspect the relevant implementation and its tests. Check the working-tree status before editing so existing user changes are not overwritten.
2. Make only the necessary edits. Preserve unrelated changes already present in the working tree.
3. Run the most relevant available tests, linting, or validation for the changed area. Do not run destructive commands or broad migrations without authorization.
4. Review the diff for correctness, unintended changes, new dependencies, and scope creep.
5. Stop when the requested outcome is met. Do not continue with speculative improvements.

## Report results honestly

- Summarize what changed and name the files changed.
- State which checks actually ran and whether they passed. Never say a change was tested if tests were not run.
- Note any unverified behavior, blocked checks, assumptions, or remaining risks concisely.
- If no change was needed, explain the repository evidence supporting that conclusion.

## Working rule

**Inspect first. Change only what was requested. Verify what you can. Report facts, not guesses.**
