# AgentForge Clinical Guideline Corpus — Notice and Attribution

> **Status: demo stub only.** This corpus is project-prepared summary
> material chosen to exercise the retrieval pipeline end-to-end during
> the W2 demo. It has NOT been clinically reviewed, NOT been approved
> by any care-delivery organization, and is NOT the corpus a production
> deployment would ship. Treat it as scaffolding. See "Demo scope vs
> production" below for what a production corpus would look like.

The markdown documents under this directory are **brief factual
summaries** of publicly available clinical guidance, prepared for the
AgentForge Clinical Co-Pilot's evidence-retrieval demo. They are NOT
clinical advice, NOT a substitute for the source guidelines, and NOT
exhaustive. The sidecar's evidence retriever loads these chunks as a
corpus the agent can cite from at demo time.

## What lives here

`{topic}/{document}.md` — one markdown file per guideline summary.
Each document carries a `SOURCE:` block at the top citing the
authoritative publication it summarizes.

`index.json` — pre-chunked metadata produced by
`sidecar/scripts/chunk_guidelines.py`. Each chunk has:

- `doc_id` — stable handle for the source document (e.g.
  `ada_standards_2024_glycemic_targets`)
- `section` — the heading the chunk falls under
- `version` — guideline year / version string
- `chunk_id` — globally unique identifier (`{doc_id}::{section_slug}::{n}`)
- `text` — the chunk body, ~500 tokens (configurable in the script)

## Attribution

Sources cited in individual documents are the property of their
respective organizations. The summaries here are short factual
restatements suitable for an academic demo; for any production use,
the agent should be configured to cite the source URLs directly and
to defer to the original publications.

Where a document quotes specific numeric thresholds (A1C targets, BP
targets, GFR cutoffs), those are the values published by the named
guideline at the cited version. They are NOT recommendations from
this project — we are restating, not prescribing.

## Demo scope vs production

The W2 demo corpus is **a starter set** — five representative topics
(diabetes, hypertension, lipid management, CKD staging, common lab
interpretation) chosen to exercise the retrieval pipeline end-to-end.
Production deployment expects:

- A larger, full-coverage corpus (~30+ guideline documents)
- Direct ingestion from upstream sources rather than hand-summarized
  markdown
- A re-chunking pipeline that runs on guideline-update events, not
  manually

Task 10 commits the demo corpus and the chunking script. Building out
the production-grade corpus is a follow-up task.

## License

The chunking script and metadata format are part of OpenEMR and
licensed GPL-3.0-or-later. The summary text in each document is
*derived from* publicly available guideline material — see each
document's `SOURCE:` block. Where summaries quote phrases verbatim,
those quotes remain the property of the originating organization.
