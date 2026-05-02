# Prompts — v1

Initial externalisation of the AgentForge MVP prompts. The bodies were
extracted from Python string constants by Task 43; their content is the
canonical Task 51.3 ruleset (section headers, citation types, demographic
weaving, out-of-scope guardrail).

| File              | Migrated from                                                  |
| ----------------- | -------------------------------------------------------------- |
| `synthesizer.md`  | `agentforge.orchestrator.SYSTEM_PROMPT`                        |
| `planner.md`      | `agentforge.orchestrator.planner.PLANNER_SYSTEM_PROMPT`        |

Once a prompt body is committed in this directory, treat it as
immutable. Edits land as `v2/`, not as in-place changes here, so
deployed prompts stay reproducible from any past commit. See the
top-level `prompts/README.md` for the full versioning policy.
