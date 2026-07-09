# AI Behavior Rules for OpenSecDash

These rules apply to all AI assistants, coding agents, and automated tools working on this repository. They are specific to this project but independent of any particular AI system.

## 1. Questions take priority over changes

- If a prompt contains a recognizable question, **no changes to the repository** may be made.
- In that case, the question must be answered first and exclusively.
- This also applies if the prompt additionally contains a concrete instruction or task.
- Only after the question has been answered may the AI offer to execute the included instruction or ask whether it should proceed.
- Examples of questions include direct questions with a question mark as well as indirect questions such as “Can you explain...”, “Do you know...”, “What does... mean?”, “Should we...”, or “How does... work?”.

## 2. Respect the project context

- OpenSecDash is an open-source security dashboard focused on homelabs.
- Changes should respect this focus: practical security visibility, understandable investigation workflows, and ease of use for self-hosting and homelab setups.
- The documentation under `website/` is an important source for features, terminology, and expected behavior.
- User-facing communication, documentation, and UI text should be clear, understandable, and avoid unnecessary enterprise jargon.

## 3. Follow and maintain ADRs

- Architecture Decision Records under `docs/adr/` are binding project guidance and must be followed when making changes.
- Before implementing architecture, data model, plugin, UI, security, deployment, or workflow changes, check the relevant ADRs.
- If a user explicitly intends a change that conflicts with an existing ADR, do not silently ignore the ADR. Point out the conflict and ask whether the ADR should be updated.
- If the requested change intentionally changes an architectural decision, update the affected ADR or add a new ADR as part of the same work.
- Do not invent ADR content. ADR updates must reflect the user's intended change and the actual implementation.

## 4. Security and privacy

- Do not commit secrets, tokens, passwords, private IP lists, or sensitive logs in examples, documentation, or source files.
- Do not add telemetry, uploads, or external data flows without explicitly documenting and justifying them.
- Security features should remain transparent and understandable.
- Be conservative with security-relevant changes and ask for clarification if risks are unclear.

## 5. Respect the Insights engine

- The Insights engine is a core feature of OpenSecDash.
- Heuristic web-probe detection should preferably be maintained through declarative rules rather than unnecessarily complex code.
- Do not add remote code execution, `eval`, dynamic imports, or freely configurable remote rule sources for insights.
- Rules must remain understandable, validatable, and safely updateable.

## 6. Keep changes intentional and minimal

- Only change files that are necessary for the task.
- Do not perform broad refactorings, formatting changes, or restructuring as a side effect.
- Respect the existing architecture, plugin structure, and documentation style.
- If something is unclear, first explain what is unclear and ask for a decision.

## 7. Tests and quality

- After code changes, run appropriate tests, type checks, or builds when practical.
- If tests were not run, clearly mention this in the final response.
- Do not hide missing or failing tests.
- Do not present changes as complete if known errors remain unresolved.

## 8. Keep documentation current

- New features, configuration options, plugins, or security-relevant behavior changes must be documented.
- Website documentation under `website/guide/` should match the actual behavior.
- Examples should be realistic for homelabs and self-hosting.

## 9. Communication

- Responses should be concise, clear, and action-oriented.
- Always clearly name changed files.
- Do not perform unrequested changes.
- If multiple reasonable approaches exist and the decision affects architecture, security, or user experience, ask first.
