# Prompt Library

Versioned, text-reviewable prompt files for the AgentForge sidecar. Each
prompt body lives in a `vN/` subdirectory as a Markdown file; `version.json`
pins which version each component is currently using.

## Layout

```
prompts/
├── v1/
│   ├── synthesizer.md   # Post-tool-result synthesis prompt
│   ├── planner.md       # Use-case classification + dispatch plan
│   └── README.md        # Per-version notes
├── version.json         # {"<component>": "<version>"} mapping
└── README.md            # This file
```

## Why versioned files

Inline Python string constants make prompt drift invisible in code review:
a one-character change to a clinical safety rule looks the same as a
typo fix. Externalising to versioned `.md` files lets future prompt edits
land as readable text diffs and keeps the active version pinned per
release.

The version directory (`v1/`, `v2/`, ...) is the unit of change. Once
shipped, a version directory is immutable — fixing a typo in a released
prompt means cutting a new version (`v2/`) and flipping the entry in
`version.json`. That keeps deployed prompts reproducible from any past
commit.

## Loading

Sidecar code reads prompts via `agentforge.prompts.load_prompt(component)`
(see `sidecar/src/agentforge/prompts.py`). The loader:

1. Reads `version.json` to find the active version directory for the
   requested component.
2. Reads `<version>/<component>.md`.
3. Strips the YAML frontmatter and surrounding whitespace.
4. Returns the prompt body as a `str`.

The result is cached via `functools.lru_cache`, so loading happens once
per process per component.

## Frontmatter

Every prompt file starts with a small YAML frontmatter block:

```
---
version: v1.0.0
purpose: One-line description of what this prompt is for
last_modified: YYYY-MM-DD
---
```

The frontmatter is for humans reading the file directly; the loader
strips it before returning the body.

## Components covered

| Component     | File                | Used by                                                    |
| ------------- | ------------------- | ---------------------------------------------------------- |
| `synthesizer` | `vN/synthesizer.md` | `agentforge.orchestrator.SYSTEM_PROMPT`                    |
| `planner`     | `vN/planner.md`     | `agentforge.orchestrator.planner.PLANNER_SYSTEM_PROMPT`    |

## Components intentionally NOT covered

- **`verifier`** — the streaming verifier (`agentforge.verifier`) is a
  deterministic sentence-level grounding checker. It does not invoke an
  LLM and therefore has no system prompt to externalise. The original
  Task 43 spec listed `verifier.md`; it was dropped because there was
  nothing to put in it.
- **`use_case_taxonomy`** — the four planner use cases
  (`admit_synthesis`, `contraindication`, `delta_computation`, `followup`)
  are enumerated in code as the `UseCase` `StrEnum` in
  `sidecar/src/agentforge/orchestrator/planner.py`. The planner prompt
  references them by name, but the taxonomy itself is a code construct,
  not a prompt.

## Cutting a new version

1. Copy `vN/` to `vN+1/`.
2. Edit prompts in `vN+1/`. Bump the `version:` and `last_modified:`
   fields in each touched file's frontmatter.
3. Update the corresponding entries in `version.json` to `vN+1`.
4. Land the change in a single commit so the active-version flip and the
   prompt edits review together.

The existing `vN/` directory is left in place — that's deliberate, so a
hot rollback is a one-line `version.json` edit.
