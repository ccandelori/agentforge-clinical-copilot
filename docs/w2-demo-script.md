# W2 Demo Video — Script & Shot List

> Target: a single 3–5 minute screen recording covering the W2 thesis end
> to end, matching the W2 brief's Submission Requirements ("Demo Video —
> 3–5 minutes showing document upload, extraction, evidence retrieval,
> citations, eval results, and observability"). The grader watches this
> without sitting through the live defense, so the script has to stand
> alone. Designed for one or two takes — every segment is timed, every
> action is named, and every talking point is a quote-block ready to read
> verbatim.
>
> The actual recording is a separate human step. This document is the
> read-aloud script, the shot list, and the pre/post-record runbook.
>
> **Total runtime budget: 4:00 ± 30 seconds.** If any segment overshoots,
> the cuttable beats are flagged in *Watch out for*.

---

## At-a-glance shot list

| # | Range       | Surface                     | One-line purpose                                 |
|---|-------------|-----------------------------|--------------------------------------------------|
| 1 | 0:00 – 0:15 | Browser, full-screen        | Thesis cold open over the live URL               |
| 2 | 0:15 – 0:45 | Patient dashboard (Synthea persona pid 22) | "What you're looking at" — the Vue port itself, against a chart with real data |
| 3 | 0:45 – 1:45 | AgentForge drawer + chat (still pid 22)    | One chart Q&A → guideline RAG via "Guidelines" toggle |
| 4 | 1:45 – 3:15 | **Switch to Chen** + Drawer + DocumentViewer modal | Doc-upload pipeline → bbox citation overlay      |
| 5 | 3:15 – 3:45 | Terminal (full-screen)      | Eval gate self-test as the correctness claim     |
| 6 | 3:45 – 4:00 | Browser (live URL again)    | Close + repo pointer                             |

**Two on-screen surfaces only:** browser at
`https://143.244.157.90:9300/dashboard/` and a single full-screen
terminal. No multi-window choreography. Switch with one Cmd-Tab.

---

## Pre-record checklist

Run these in order. Don't start recording until every box is checked —
the demo script assumes a warm system.

- [ ] **Droplet warm-up (twice).** A cold sidecar costs 2–4s on the
      first request and that pause shows up at minute 3.

      ```bash
      ./scripts/deploy-droplet.sh check
      sleep 5
      ./scripts/deploy-droplet.sh check
      ```

      Both runs should print `sidecar /health responded`. If either
      fails, fix that before recording.

- [ ] **Browser at the right zoom.** Set Chrome / Safari to 110%
      zoom. The dashboard and the chat drawer both fit cleanly on a
      1440×900 capture at 110%; smaller and the bbox overlay's blue
      rectangles read as fuzz.

- [ ] **Self-signed cert pre-accepted.** Visit
      `https://143.244.157.90:9300/dashboard/` once and accept the
      certificate so the on-screen flow doesn't show the browser warning
      page mid-recording.

- [ ] **Logged in already.** Log in as the demo user before pressing
      record so the OAuth bounce doesn't burn 8 seconds of the cold
      open. Park the browser on the dashboard's patient list view.

- [ ] **Personas seeded — both pools.**
      - **Pool A (Synthea-rich, used for dashboard tour + chart Q&A):**
        Confirm pid 22 (Nichelle912 Johnston597) is searchable —
        Synthea-imported patients are loaded by the dev-easy bake;
        no separate seed step. Backup persona: pid 8 (Eula461 Crist667).
      - **Pool B (W2 demo personas, used for upload demo + Care Team):**
        Confirm Margaret Chen (`MRN-2026-04481`, pid 29) is searchable.
        If not:
        ```bash
        ssh root@143.244.157.90 \
          'docker exec development-easy-openemr-1 \
           php /openemr/scripts/seed-demo-patients.php'
        ```
        The script is idempotent on `pubpid`. Chen's chart will be
        empty by design — that's the point of the upload segment.

- [ ] **Conversation context cleared.** Open pid 22 (Nichelle Johnston),
      open the AgentForge drawer, click the conversation menu →
      **Reset context** so the recording starts on a fresh thread.
      A drawer carrying eight prior turns is visually noisy and raises
      questions the script doesn't answer. Repeat for Chen so segment
      4 starts clean too.

- [ ] **Terminal window prepped.** Open a single terminal at
      `~/Desktop/Gauntlet/openemr/sidecar` (or whichever working copy
      will run pytest). Resize to a screen-capture-friendly size
      (~120 cols × 30 rows). Pre-type the gate-self-test command but
      *don't* press enter yet. **The `-m gate_validation` flag is
      mandatory** — the test is deselected by default and without the
      marker pytest collects 0 tests, killing the segment.

      ```bash
      cd sidecar
      uv run pytest tests/eval/gate/test_gate_blocks_regression.py \
        -m gate_validation -v
      ```

- [ ] **Notifications silenced.** macOS DND on. Close Slack, mail,
      anything that posts banners. A toast in the top-right of a 4-min
      take is a re-record.

- [ ] **Mic check.** 30 seconds of quiet recording, listen back. If
      there's HVAC hum, move closer to the mic. The script reads
      ~700 words at this length; mic clarity matters.

---

## 0:00 – 0:15 · Cold open

**Surface.** Browser at `https://143.244.157.90:9300/dashboard/`,
parked on the patient list view (logged in, no chart open yet).

**Action.**

1. Start with the address bar in focus so the live URL is the first
   thing on screen for ~2 seconds.
2. Click into the patient list. Don't open a chart yet — that's the
   next segment.

> "AgentForge: a Vue 3 patient dashboard with a clinical co-pilot,
> running at this URL. Every claim it makes carries a citation the
> clinician can verify."

**Watch out for.**

- *Don't* read the URL out loud — it's on screen.
- If the page paints slowly, the address bar still anchors the shot.
  Don't re-load mid-narration.

---

## 0:15 – 0:45 · The dashboard itself

**Surface.** Click into the Synthea-rich persona at pid 22
(Nichelle912 Johnston597 — the numeric suffix is a Synthea-import
artifact; spoken as "Nichelle Johnston").

**Action.**

1. Search "Johnston" in the patient list, click into pid 22.
2. Let the dashboard paint. One quick scroll top-to-bottom so the
   header band, vitals strip, and the seven cards (Allergies, Problem
   list, Medications, Prescriptions, Care Team, Recent encounters, Lab
   results) are all visible. Don't dwell on any single card.

> "The patient dashboard, ported from PHP-rendered server pages to
> Vue 3 against the existing FHIR R4 API. The OpenEMR backend is
> untouched — a thin sidecar brokers OAuth so the access token never
> touches JavaScript, then forwards FHIR calls. Seven cards plus
> vitals, all driven from the same FHIR queries OpenEMR already
> exposes."

**Watch out for.**

- Synthea-imported names carry numeric suffixes (`Nichelle912`,
  `Johnston597`). Narrate as "Nichelle Johnston" without spelling
  out the numbers.
- Synthea Problem List items are all `category=encounter-diagnosis`,
  and lab/vitals tables render empty `referenceRange` /
  `interpretation` columns. Don't dwell — the cards-as-FHIR-renderers
  point lands either way.
- If the EncountersCard is slow to paint, scroll past it.

---

## 0:45 – 1:45 · AgentForge drawer + chart Q&A + guideline toggle

**Surface.** AgentForge drawer (right edge of dashboard) +
`AgentChatPane` composer.

**Action.**

1. Click the AgentForge handle on the right edge → drawer slides in.
2. Type into the composer: **"Summarize Nichelle's last visit and
   any active problems."**
3. Press Send. Wait for the streamed reply (~3-5s). Hover a citation
   pill so the cursor lands on it; click it so the CitationsPane
   expands with the source quote.
4. In the composer toolbar, click the **Guidelines** toggle (small
   uppercase chip next to the paperclip — tooltip "Toggle to ask a
   clinical-guideline question"). The button highlights.
5. Type: **"How should I manage CKD stage 3?"** Send.
6. Wait for the reply. Inline citations should be guideline-shaped
   (corpus snippets), not chart-shaped. Hover one.
7. Toggle Guidelines off afterwards so segment 4 starts clean.

> "The co-pilot is a top-level drawer scoped to the active patient.
> Every claim carries a citation back to the chart — the pills are
> clickable and expand to the FHIR resource the answer came from.
>
> Now I'll flip the *Guidelines* toggle. Off, the agent runs the
> chart-Q&A loop only. On, the same turn also routes through a
> hybrid-RAG retriever over a clinical guideline corpus, and the
> response cites the guideline alongside the chart. Visible
> affordance over auto-detection — false positives turn a chart
> question into a guideline lookup behind the user's back."

**Watch out for.**

- The first chart-Q&A turn after a sidecar restart can take 8–12s
  (cold LangGraph build + Anthropic round-trip). The
  `deploy-droplet.sh check ; check` warm-up mitigates this.
- The guideline corpus is *project-prepared summaries*, framed in
  `sidecar/data/guidelines/NOTICE.md` as "demo stub only." Don't
  claim production-grade ingestion.
- If the planner-Haiku tool-call fallback warning fires, the reply
  still completes. Mention only if asked.
- Don't let the guideline reply run to 15+ inline citations. If the
  cite list is long, scroll the bubble so the cursor lands on a
  specific pill.

---

## 1:45 – 3:15 · Doc-upload pipeline (the headline)

**Surface.** Switch patients first (Nichelle → Margaret Chen). Drawer
re-mounts on the new chart. The `DocumentViewer` modal opens at the
end.

**Action.**

1. Patient picker → search "Chen" → click Margaret Chen
   (`MRN-2026-04481`, pid 29). The dashboard paints — and the cards
   are mostly empty. **That's the point.** Brief pause; let the
   visual land before the narration.
2. Click the paperclip in the composer toolbar (`data-test="attach-button"`).
3. In the file picker, attach
   `week2/example-documents/intake-forms/p01-chen-intake-typed.pdf`.
   (Pre-stage the path in your file dialog's recents.)
4. The composer shows a pending-attachment chip. Type:
   **"Extract this intake form."** Send.
5. The reply takes ~12-15s on a warm sidecar. **Don't fill the
   silence with rambling** — the script below is paced to fill it.
   The chat reply lists extracted fields; below the bubble,
   `<ExtractionPanel>` renders with extracted demographics, allergies,
   medications, etc.
6. Click **"View source (18)"** at the foot of the panel. The
   `DocumentViewer` modal opens.
7. The PDF renders inside the modal, with blue rectangles overlaying
   the regions the model attributed each field to.
8. Hover over one rectangle so the cursor lands on it; the
   corresponding field highlights in the side list.
9. Close the modal (Esc or the X).

> *(opening Chen's empty chart)*
>
> "Switching to Margaret Chen — a brand-new patient, no chart history.
> The cards are empty. The agent doesn't just synthesize *existing*
> charts; it ingests *new* clinical context."
>
> *(attach + send)*
>
> "Now the headline. I'm attaching a scanned intake form — typed PDF,
> handwritten signature — and asking the agent to extract it. The
> sidecar runs Claude Haiku as a vision model against rendered page
> images, parses the response into a Pydantic schema, and persists
> the result as a FHIR `QuestionnaireResponse` through the existing
> OpenEMR service layer. Persistence runs server-side, so the audit
> log is single-sourced through the canonical save path."
>
> *(while waiting for the extraction)*
>
> "Extraction surfaces as a *suggestion* with citations, not a write
> to the canonical clinical tables. OCR is fallible — an intake form
> that misreads `PCN` as `Pen-V` would land as a charted allergy with
> no clinician in the loop. Promotion to the chart is an explicit
> human action."
>
> *(click View source, modal opens)*
>
> "Here's the trust artifact. Every extracted field has a bounding
> box back to the region of the PDF the model thinks it read it
> from. A clinician verifying a field clicks through, sees the
> source pixels, and decides. The citation story for vision."

**Watch out for.**

- The `View source (N)` button label varies — `N` is whatever bbox
  count the extraction produced (usually 12–22 for the Chen intake).
  Read the literal number on screen, don't rehearse "(18)".
- If the modal renders the PDF *without* rectangles, the bbox overlay
  didn't load (rendering race). Close, wait one second, click "View
  source" again.
- PNG personas (Reyes, Kowalski) won't render in the modal — PDF.js
  doesn't parse `image/png` bytes. **Typed PDFs only**
  (Chen, Whitaker). Fallback persona: Whitaker (`p02-whitaker-intake.pdf`).
- Bbox placement is approximate (Haiku-vision lands the right
  region, sometimes one row off). Don't pixel-peep on camera.
- If the extraction fails (rare), skip to the eval segment and
  explicitly call out: "doc-upload pipeline is shipped, demoed in
  the live defense." Try once more first.

---

## 3:15 – 3:45 · Eval pipeline as correctness claim

**Surface.** Cmd-Tab to the prepped terminal. Full-screen.

**Action.**

1. The command is already typed; press Enter.

   ```bash
   cd sidecar
   uv run pytest tests/eval/gate/test_gate_blocks_regression.py \
     -m gate_validation -v
   ```

2. Watch the test run. It loads the 50-case YAML suite, scores per
   category, feeds the aggregate to the gate, and asserts the gate
   returns a *failing* verdict on a deliberately regressed adapter.
   Runtime ~6-15s.
3. The test passes (the gate caught the regression). Scroll up so
   the test name and the PASSED line are both visible.

> "A gate self-test against the eval pipeline. The fifty-case golden
> suite scores five boolean rubrics per case — schema validity,
> citation present, factually consistent, safe refusal, and
> **no PHI in logs**. This run feeds the agent through an adapter
> that deliberately fabricates `A1c = 15.5%` with the citation
> stripped, and asserts the gate *fails*. Test passes when the gate
> would have blocked the regression. That's the citation contract
> AND the PHI-containment contract enforced together at build time,
> not at audit time. The sidecar uses HMAC pseudonyms for any
> identifier that crosses the observability boundary — same pattern
> we use for span IDs in Langfuse, so traces are useful for
> debugging without ever carrying raw patient strings."

**Watch out for.**

- The test is marked `gate_validation` and is **deselected by
  default**. The `-m gate_validation` flag is mandatory; without it
  pytest reports `0 tests collected` and the segment dies.
- If the run is unexpectedly slow (>30s), cancel with Ctrl-C and
  re-run — the harness is mock-LLM-only by design but imports can
  be slow on first load.
- If the test *fails* (gate didn't catch the regression), don't
  debug on camera — that's a real bug and a re-record. Stop the take.
- The baseline is **measured** (`baselines/week2.json` =
  `_meta.status: "measured"`, $1.54 real-LLM run on 2026-05-09 —
  see `docs/w2-cost-latency-report.md`). Two-leg correctness story
  per `docs/defense-qa-w2.md` Q15: the gate self-test proves the
  gate's logic catches regressions; the measured anchor proves it's
  calibrated against real agent behavior, not idealized stubs.
  Don't volunteer this in voiceover, but know the answer if asked.
- If a grader asks "show me PHI doesn't leak in stdout" off-script,
  Cmd-Tab back, run `ssh root@<droplet> 'docker logs --tail 200
  agentforge-sidecar 2>&1 | grep -iE "MRN|chen|whitaker"'` — should
  return empty. (Stdout is uvicorn access logs only by design;
  PHI-bearing data lives in HMAC-pseudonymized observability spans,
  not stdout.)

---

## 3:45 – 4:00 · Close

**Surface.** Cmd-Tab back to the browser. Patient dashboard with
drawer open is fine; the address bar is the visual anchor.

**Action.**

1. Click into the address bar so the URL is visible again.
2. Hold the shot.

> "Vue 3 dashboard against FHIR, an AgentForge co-pilot with
> verifiable citations for chart and guideline questions, a
> vision-extraction pipeline with bbox citations, and a gate that
> proves it catches the class of regression we'd most want to catch.
> Code at labs.gauntletai.com/cameroncandelori/openemr."

**Watch out for.**

- Aim for ~12 seconds of voice + 3 seconds of held-shot silence at
  the end so the editor has clean audio to fade out under.
- Don't re-list the personas, the eval-suite size, or the deviation
  count. Thesis recap, not a feature inventory.

---

## Post-record checklist

Run these before publishing the cut.

- [ ] **Blur or scrub any visible OAuth client_secret.** The
      `.env` file shouldn't be on screen at any point, but if a
      terminal scroll-back accidentally exposed it, blur the
      relevant frames or re-record. The client_id was rotated on
      2026-05-08 specifically because of a prior leak — don't
      repeat the mistake on video.
- [ ] **Trim long pauses at minutes 2-3.** The doc-upload
      extraction wait is ~12-15s of dead air. Cut to ~5-6s on
      camera and let the voiceover bridge the rest. The bbox
      reveal is the payoff; the wait is not the show.
- [ ] **Verify the live URL is legible** in the cold open and the
      close. If 110% zoom isn't readable on a 720p export,
      bump to 125% in the recording or add a lower-third title
      card with the URL.
- [ ] **Audio levels.** Voice peak between -6 and -3 dB. The
      pytest-running terminal has no audio; no balance pass
      needed unless you added music.
- [ ] **Closed captions / transcript.** Generate from the script
      above (it is the canonical voiceover), correct the few
      proper nouns (Pinia, FastAPI, LangGraph, FHIR, OAuth2,
      Anthropic, Haiku, OpenEMR, AgentForge, Whitaker, Chen),
      and ship as a sidecar `.vtt`. Graders may have audio off.
- [ ] **Final length.** Trim or re-pace to land between 3:30 and
      4:30. Per the W2 brief the window is 3–5 minutes; under 3:00
      reads as "didn't cover the material," over 5:00 falls outside
      the spec. 4:00 is the target.
- [ ] **Export.** 1080p, H.264, target ~50 MB (the GitLab MR
      attachment limit and most cohort-submission portals both
      tolerate ≤100 MB easily).

---

## Fallback decision tree

If a segment dies on take, here's the order of preference for
recovering without re-recording the whole thing:

1. **Cold open** dies → re-take just the cold open. Two-take
   composite is invisible to the viewer if the dashboard is in
   the same scroll position.
2. **Dashboard scroll** dies → re-take, same. Easy splice point
   at "Now the AgentForge drawer is the right-edge handle…".
3. **Chart Q&A or guideline turn** flakes → re-record the segment
   only. Reset the conversation context first
   (drawer menu → Reset context).
4. **Doc-upload extraction fails** → see "Watch out for" above. If
   the second attempt also fails, redo the take with persona
   Whitaker (`p02-whitaker-intake.pdf`) — same script works
   verbatim; just say "Whitaker" instead of "Margaret's intake."
5. **Gate self-test fails** → stop the take. That's a real
   regression in the gate; debug it before recording continues.
6. **Close** dies → re-take. Trivial.

If two complete end-to-end takes are budget, alternate which
persona drives the doc-upload segment so a flaky extraction in one
take doesn't block the other.
