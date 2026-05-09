# W2 Demo Video — Script & Shot List

> Target: a single 5–7 minute screen recording covering the W2 thesis end
> to end. The grader watches this without sitting through the live
> defense, so the script has to stand alone. Designed for one or two
> takes — every segment is timed, every action is named, and every
> talking point is a quote-block ready to read verbatim.
>
> The actual recording is a separate human step. This document is the
> read-aloud script, the shot list, and the pre/post-record runbook.
>
> **Total runtime budget: 6:00 ± 30 seconds.** If any segment overshoots,
> the cuttable beats are flagged in *Watch out for*.

---

## At-a-glance shot list

| # | Range       | Surface                     | One-line purpose                                 |
|---|-------------|-----------------------------|--------------------------------------------------|
| 1 | 0:00 – 0:30 | Browser, full-screen        | Thesis cold open over the live URL               |
| 2 | 0:30 – 1:30 | Patient dashboard (Synthea persona pid 22) | "What you're looking at" — the Vue port itself, against a chart with real data |
| 3 | 1:30 – 2:45 | AgentForge drawer + chat (still pid 22)    | Chart Q&A → guideline RAG via "Guidelines" toggle |
| 4 | 2:45 – 4:30 | **Switch to Chen** + Drawer + DocumentViewer modal | Doc-upload pipeline → bbox citation overlay      |
| 5 | 4:30 – 5:30 | Terminal (full-screen)      | Eval gate self-test as the correctness claim     |
| 6 | 5:30 – 6:00 | Browser (live URL again)    | Close + repo pointer                             |

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
      *don't* press enter yet.

      ```bash
      cd sidecar
      uv run pytest tests/eval/gate/test_gate_blocks_regression.py \
        -m gate_validation -v
      ```

- [ ] **Notifications silenced.** macOS DND on. Close Slack, mail,
      anything that posts banners. A toast in the top-right of a 6-min
      take is a re-record.

- [ ] **Mic check.** 30 seconds of quiet recording, listen back. If
      there's HVAC hum, move closer to the mic. The script reads
      ~1100 words; mic clarity matters.

---

## 0:00 – 0:30 · Cold open

**Surface.** Browser at `https://143.244.157.90:9300/dashboard/`,
parked on the patient list view (logged in, no chart open yet).

**Action.**

1. Start with the address bar in focus so the live URL is the first
   thing on screen for ~3 seconds.
2. Click into the patient list. Don't open a chart yet — that's the
   next segment. The visual goal is "this is a real, deployed
   surface" before the narration explains why it exists.

> "Clinicians lose thirty to sixty minutes a day to chart synthesis —
> reading the same patient's history four times in four contexts.
> AgentForge gives that time back, with citations the clinician can
> click through and verify. Everything you're about to see is running
> on a single droplet at this URL. The patient data is synthetic; the
> code is shipped."

**Watch out for.**

- *Don't* read the URL out loud — it's on screen. Forty seconds of
  voiceover in a thirty-second segment is the most common over-run.
- If the page paints slowly, the address bar still anchors the shot.
  Don't re-load mid-narration.

---

## 0:30 – 1:30 · The dashboard itself

**Surface.** Click into the Synthea-rich persona at pid 22
(Nichelle912 Johnston597 — the numeric suffix is a Synthea-import
artifact; spoken as "Nichelle Johnston"). The shipped surface is
one scroll: header band → vitals strip → seven cards (Allergies,
Problem list, Medications, Prescriptions, Care Team, Recent
encounters, Lab results).

**Action.**

1. Search "Johnston" in the patient list, click into pid 22.
2. Let the dashboard paint. Hover over a card briefly so the cursor
   shows what's interactive.
3. Scroll once, slowly, top to bottom — header → VitalsStrip →
   Allergies (≥6 rows) → Problem list (deep) → Medications →
   Prescriptions → Care Team → Recent encounters → Lab results.
   Don't click into anything; the next segment opens the drawer.

> "This is the patient dashboard, ported from PHP-rendered server
> pages to Vue 3 against the existing FHIR R4 API. The OpenEMR
> backend is untouched apart from a thin backend-for-frontend on the
> AgentForge sidecar — it brokers OAuth so the access token never
> touches JavaScript, and forwards FHIR calls. Seven cards — Allergies,
> Problem list, Medications, Prescriptions, Care Team, Recent
> Encounters, Labs — plus a vitals strip and the patient header band,
> all driven from the same FHIR queries OpenEMR's own API already
> exposes. The defense doc walks through *why Vue 3 specifically*
> over React, Svelte, and Qwik — the headline is that the
> architectural win is the separation, not the framework."

**Watch out for.**

- Synthea-imported names carry numeric suffixes (`Nichelle912`,
  `Johnston597`). On screen they look synthetic; narrate the
  patient as "Nichelle Johnston" without spelling out the numbers.
  Optional polish: `UPDATE patient_data SET fname='Nichelle',
  lname='Johnston' WHERE pid=22;` on the droplet before recording
  (cosmetic; no functional impact).
- Synthea Problem List items are all `category=encounter-diagnosis`
  rather than `problem-list-item`; if the card filter is strict you
  may see fewer rows than expected. The card still demonstrates the
  FHIR-renderer point.
- Lab / vitals tables on Synthea-imported personas render empty
  ranges (no `referenceRange` / `interpretation`). Don't dwell;
  the cards-as-FHIR-renderers point lands either way.
- If the EncountersCard is slow to paint, scroll past it — the
  narration is generic and doesn't depend on a specific row.

---

## 1:30 – 2:45 · AgentForge drawer + chart Q&A

**Surface.** AgentForge drawer (right edge of dashboard) +
`AgentChatPane` composer.

**Action.**

1. Click the AgentForge handle on the right edge → drawer slides in.
2. Type into the composer: **"Summarize Nichelle's last visit and
   any active problems."**
3. Press Send. Wait for the streamed reply — should be ~3-5s.
4. Point (cursor hover) at a citation pill in the bubble. Click it
   so the CitationsPane expands and the source quote shows.
5. Now the guideline beat. In the composer toolbar, click the
   **Guidelines** toggle (the small uppercase chip next to the
   paperclip — its tooltip reads "Toggle to ask a clinical-guideline
   question"). The button highlights.
6. Type: **"How should I manage CKD stage 3?"** Send.
7. Wait for the reply. Inline citations should be guideline-shaped
   (corpus snippets), not chart-shaped. Hover one.
8. Toggle Guidelines off afterwards so the next segment isn't
   carrying state.

> "The co-pilot is a top-level drawer scoped to the active patient.
> Every claim it makes carries a citation back to the chart — those
> pills are clickable, and they expand to the FHIR resource the
> answer came from. Now I'll flip the *Guidelines* toggle. With it
> off, the agent runs the chart-Q&A loop only. With it on, the same
> turn also routes through a hybrid-RAG retriever over a clinical
> guideline corpus, and the response cites the guideline alongside
> the chart. The toggle is a deliberate visible affordance — we
> chose it over auto-detecting intent from message text because
> false positives turn a chart question into a guideline lookup
> behind the user's back."

**Watch out for.**

- The first chart-Q&A turn after a sidecar restart can take 8–12s
  (cold LangGraph build + Anthropic round-trip). The `deploy-droplet.sh
  check ; check` warm-up is what mitigates this — if the first turn
  still hangs, it's a sign the warm-up didn't take.
- The guideline corpus is *project-prepared summaries*, framed in
  `sidecar/data/guidelines/NOTICE.md` as "demo stub only." If a
  grader watches and Googles the wording: that's fine, the framing
  document calls it out. Don't claim production-grade ingestion.
- If the planner-Haiku tool-call fallback warning fires, the reply
  still completes — it just uses the default plan. No on-screen
  signal; mention only if asked.
- Don't let the guideline reply run to 15+ inline citations. If the
  cite list is long, scroll the bubble so the cursor lands on a
  specific pill.

---

## 2:45 – 4:30 · Doc-upload pipeline (the headline)

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
> "Now switching to Margaret Chen — a brand-new patient with no
> chart history yet. The cards are empty. This is the second use
> case: the agent doesn't just synthesize *existing* charts, it
> ingests *new* clinical context."
>
> *(attach + send)*
>
> "Now the headline. I'm attaching a scanned intake form — typed PDF,
> handwritten signature — and asking the agent to extract it. The
> sidecar runs Claude Haiku as a vision model against rendered page
> images, parses the response into a Pydantic schema, and persists
> the result as a FHIR `QuestionnaireResponse` through the existing
> OpenEMR service layer. That's important: the persistence runs
> server-side from the sidecar, not from the browser, so it's a
> single round-trip from the dashboard's perspective and the audit
> log is single-sourced through the canonical save path."
>
> *(while waiting for the extraction)*
>
> "The agent doesn't write extracted demographics or allergies into
> the canonical clinical tables. OCR is fallible, and an intake form
> that misreads `PCN` as `Pen-V` would land as a charted allergy
> with no clinician in the loop. So extraction surfaces as a
> *suggestion* with citations; promotion to the chart is an explicit
> human action."
>
> *(click View source, modal opens)*
>
> "And here's the trust artifact. Every extracted field has a
> bounding box back to the region of the PDF the model thinks it
> read it from. A clinician verifying an extracted field clicks
> through, sees the source pixels, and decides. This is the citation
> story for vision: the equivalent of a quote-and-page-number for
> a structured field on a scanned form."

**Watch out for.**

- The `View source (N)` button label varies — `N` is whatever bbox
  count the extraction produced (usually 12–22 for the Chen intake).
  Read the literal number on screen, don't rehearse "(18)".
- If the modal renders the PDF *without* rectangles, that means the
  bbox overlay didn't load — probably a rendering race. Close, wait
  one second, click "View source" again.
- PNG personas (Reyes, Kowalski) won't render in the modal — PDF.js
  doesn't parse `image/png` bytes. Stick to **typed PDFs only**
  (Chen, Whitaker) for this segment. If you need a fallback persona,
  use Whitaker (`p02-whitaker-intake.pdf`).
- Bbox placement is approximate (Haiku-vision lands the right
  region, sometimes one row off). Don't pixel-peep on camera.
- If the chat reply duplicates the panel content, scroll the bubble
  so the panel is centered when you click "View source." Both the
  bubble and the panel are valid; the panel is the structured one.
- If the extraction fails (rare), your fallback is to skip to the
  eval segment and explicitly call out: "doc-upload pipeline is
  shipped, demoed in the live defense." The recording is still
  defensible — but try once more first.

---

## 4:30 – 5:30 · Eval pipeline as correctness claim

**Surface.** Cmd-Tab to the prepped terminal. Full-screen.

**Action.**

1. The command is already typed; press Enter.

   ```bash
   cd sidecar
   uv run pytest tests/eval/gate/test_gate_blocks_regression.py \
     -m gate_validation -v
   ```

2. Watch the test run. It loads the 50-case YAML suite, runs them
   through a regressed adapter, scores per category, feeds the
   aggregate to the gate, and asserts the gate returns a *failing*
   verdict. Runtime is ~6-15s depending on cache state.
3. The test passes (the gate caught the regression).
4. Scroll up in the terminal so the test name and the PASSED line
   are both visible at the end of the segment.

> "The eval pipeline isn't a regression check — it's a correctness
> claim. There's a fifty-case golden suite covering five categories:
> extraction, evidence retrieval, citations, refusal, missing data.
> What I'm running now is a *gate self-test*. It runs the full
> suite against an adapter that's been deliberately regressed —
> specifically, an adapter that asserts a clinical value with the
> citation deliberately stripped — and asserts that the gate
> *fails*. So this test passes when the gate would have blocked
> the regression. The fabricated value is `A1c = 15.5%` with no
> source attached; in production, that's exactly the failure mode
> the citation contract exists to prevent."

**Watch out for.**

- The test is marked `gate_validation` and is **deselected by
  default**. The `-m gate_validation` flag is mandatory; without it
  pytest reports `0 tests collected` and the segment dies.
- If the run is unexpectedly slow (>30s), the per-category LLM
  judge is hitting a cold network. Cancel with Ctrl-C and re-run —
  the harness is mock-LLM-only by design but the imports can be
  slow on first load.
- If the test *fails* (would mean the gate didn't catch the
  regression), don't try to debug on camera — that's a real bug
  and a re-record. Stop the take.
- Don't read the terminal scroll-back verbatim; the narration is
  the load-bearing part. Pytest output is supporting material.
- The pinned baseline is a stub (`baselines/week2.json` =
  `_meta.status: "stub"`). If a grader asks in defense, the answer
  is "the gate self-test proves the gate bites; the stub is the
  next planned regen, deferred for cost." Don't volunteer the
  caveat in the demo voiceover — keep the segment crisp.

---

## 5:30 – 6:00 · Close

**Surface.** Cmd-Tab back to the browser. Patient dashboard with
drawer open is fine; the address bar is the visual anchor.

**Action.**

1. Click into the address bar so the URL is visible again.
2. Hold the shot.

> "That's W2: a Vue 3 dashboard against FHIR, an AgentForge co-pilot
> with verifiable citations for both chart and guideline questions,
> a vision-extraction pipeline that surfaces suggestions with bbox
> citations back to the source, and an eval gate that proves it
> catches the class of regression we'd most want to catch.
> Everything is at this URL; the code and the architectural defense
> live in the GitLab repo at
> labs.gauntletai.com/cameroncandelori/openemr."

**Watch out for.**

- 30 seconds is generous for this; aim for 22 seconds of voice +
  8 seconds of held-shot silence at the end so the editor has clean
  audio to fade out under.
- Don't re-list the personas, the eval-suite size, or the deviation
  count. The close is a thesis recap, not a feature inventory.

---

## Post-record checklist

Run these before publishing the cut.

- [ ] **Blur or scrub any visible OAuth client_secret.** The
      `.env` file shouldn't be on screen at any point, but if a
      terminal scroll-back accidentally exposed it, blur the
      relevant frames or re-record. The client_id was rotated on
      2026-05-08 specifically because of a prior leak — don't
      repeat the mistake on video.
- [ ] **Trim long pauses at minutes 3-4.** The doc-upload
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
- [ ] **Final length.** Trim or re-pace to land between 5:30 and
      6:30. Anything under 5:00 reads as "didn't cover the
      material"; anything over 7:00 starts losing the grader's
      attention. Six minutes is the target.
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
