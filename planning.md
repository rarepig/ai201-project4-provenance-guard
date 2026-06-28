# Provenance Guard — Planning

## Problem & Design Philosophy

Provenance Guard is a backend that a creative-sharing platform can plug in to
estimate whether submitted text was written by a human or generated with AI,
and to surface that estimate to readers fairly.

The goal is **not** to build a perfect AI detector — perfect AI detection is an
unsolved problem. The goal is to handle an inherently uncertain ML judgment
*responsibly*: communicate uncertainty honestly, avoid wrongly accusing real
creators, and give creators a way to contest a decision.

**Core design principle — false-positive asymmetry.** On a creative platform,
labeling a human's genuine work as AI-generated is far worse than missing an
AI-generated piece. A wrongful "AI" label damages trust and the creator's
reputation. Every design decision below leans toward *protecting the human*:
when in doubt, we say "uncertain," not "AI."

---

## Architecture

```
SUBMISSION FLOW
  POST /submit ──text──► Signal 1 (LLM/Groq) ──┐
  {text, creator_id}                           │ llm_score
        │ content_id assigned                   │
        └──text──► Signal 2 (stylometry) ───────┤ stylo_score
                                                ▼
                                   ┌─────────────────────────┐
                                   │ Confidence Scorer        │
                                   │ (weighted avg + veto)    │
                                   └────────────┬────────────┘
                                                │ combined_score 0–1
                                                ▼
                                       ┌─────────────────┐
                                       │ Label Generator │ ──► 1 of 3 variants
                                       └────────┬────────┘     (AI / human / uncertain)
                                                ▼
                   ┌──────────────── Audit Log (SQLite) ──────────────┐
                   │ content_id, creator_id, ts, llm_score,           │
                   │ stylo_score, confidence, attribution, status     │
                   └──────────────────────┬───────────────────────────┘
                                          ▼
                 Response {content_id, attribution, confidence, label}

APPEAL FLOW
  POST /appeal ──► status: classified → under_review ──► Audit Log (append appeal
  {content_id, creator_reasoning}                        alongside original decision)
                                                              │
                                                              ▼
                                         Response {content_id, status, message}
```

**Submission flow:** A submission enters at `POST /submit`, receives a unique
`content_id`, and is scored independently by two signals. The Confidence Scorer
combines those two scores into one calibrated 0–1 value, the Label Generator
maps that value to one of three plain-language label variants, every value is
written to the audit log, and a structured JSON response is returned.

**Appeal flow:** A creator who disputes a result calls `POST /appeal` with the
`content_id` and their reasoning. The content's status changes from
`classified` to `under_review`, the appeal is appended to the audit log next to
the original decision, and a confirmation is returned. Automated
re-classification is intentionally out of scope; a human reviews the queue.

---

## Detection Signals

The system uses **two distinct signals** — one semantic, one structural — so
that the combination is more informative than either alone. Signals are stored
in a list internally so a third can be added later (see AI Tool Plan / stretch).

### Signal 1 — LLM classification (Groq, llama-3.3-70b-versatile)

- **What it measures:** The holistic semantic and stylistic coherence of the
  text. The model is prompted to estimate how AI-generated the text reads and
  return a structured score plus a one-line rationale.
- **Why it differs human vs. AI:** Human writing tends to make small logical
  jumps and include idiosyncratic personal detail; AI text is often smoother and
  more uniformly coherent.
- **Output:** An integer 0–100 (parsed from the model's JSON reply), normalized
  to a 0–1 score where 1 = strongly AI.
- **Blind spot:** Weak on very short text (too little to judge); the model can
  fail to recognize AI text; results can shift with prompt wording.

### Signal 2 — Stylometric heuristics (pure Python)

- **What it measures:** Measurable statistical regularity of the prose. AI text
  tends to be uniform; human writing tends to be more variable.
- **Metrics computed:**
  1. **Burstiness** — coefficient of variation of sentence length. Low variation
     → more AI-like.
  2. **Type–token ratio** — vocabulary diversity. Very smooth/repetitive
     vocabulary patterns lean AI-like.
  3. **Punctuation-density variation** — irregular human punctuation vs.
     formulaic AI patterns.
- **Why it differs human vs. AI:** These capture *structure*, not meaning — a
  genuinely independent axis from Signal 1.
- **Output:** Each metric is mapped to a 0–1 sub-score with a documented
  heuristic threshold, then averaged into one `stylo_score` (1 = strongly AI).
  Exact thresholds are calibrated against the M4 test inputs.
- **Blind spot:** Formal, uniform human writing (e.g., academic prose,
  non-native formal English) scores AI-like; AI text with deliberately injected
  variation evades it.

### Combination

`combined = 0.5 * llm_score + 0.5 * stylo_score`, then an asymmetric veto + band
mapping (see Uncertainty Representation). Equal weighting is the starting point;
it is simple to explain and revisited in M4 if testing shows one signal is
systematically more reliable.

---

## Uncertainty Representation

**Definition of `confidence`:** a single 0–1 score representing AI-likelihood.
`0` = confidently human, `1` = confidently AI, `0.5` = genuinely undecidable
(coin-flip). This is what a `0.5` means to the user: "we honestly can't tell."

**Asymmetric combination logic:**

```
combined = 0.5 * llm_score + 0.5 * stylo_score

if min(llm_score, stylo_score) < 0.35:
    attribution = "uncertain"      # VETO: one signal strongly says "human"
                                   # → never allowed to escalate to AI
elif combined >= 0.75:
    attribution = "likely_ai"      # high bar to call something AI (conservative)
elif combined <= 0.40:
    attribution = "likely_human"   # more permissive to call something human
else:
    attribution = "uncertain"
```

**Why this is asymmetric:** the AI threshold (0.75) sits far above the midpoint
(0.5), while the human threshold (0.40) sits close to it. Calling something AI
requires strong evidence; calling something human is easier. The veto means a
single strong "human" signal blocks an AI verdict outright. Both choices push
borderline cases toward "uncertain" / "human" rather than a wrongful AI label.

**Threshold table:**

| combined score        | attribution (if no veto) | label variant     |
|-----------------------|--------------------------|-------------------|
| ≥ 0.75                | likely_ai                | High-confidence AI|
| 0.40 – 0.75           | uncertain                | Uncertain         |
| ≤ 0.40                | likely_human             | High-confidence human |
| min(signal) < 0.35    | → capped at uncertain    | Uncertain         |

**Validating the scores are meaningful (M4 plan):** run at least four labeled
inputs — clearly AI, clearly human, formal-human borderline, lightly-edited-AI
borderline — and confirm the combined scores land in different bands. If a band
is unreachable or a borderline case lands wrong, print both signal scores
separately to find which one is miscalibrated and adjust the stylometric
thresholds.

---

## Transparency Label Variants

Plain language, no jargon. High-confidence labels still avoid absolute claims
and point to the appeal path.

| Variant | Trigger | Exact text |
|---------|---------|------------|
| **High-confidence AI** | attribution = likely_ai | "This content shows strong signs of being AI-generated (estimated 87% likely). This is an automated guess and can be wrong — if you wrote it yourself, you can request a review." |
| **High-confidence human** | attribution = likely_human | "This content appears to be human-written. Our analysis found no strong signs of AI generation. This is an automated estimate, not a guarantee." |
| **Uncertain** | attribution = uncertain | "We couldn't confidently tell whether this was written by a person or with AI help — the signals were mixed. We're flagging it as uncertain rather than guessing." |

(The percentage in the AI variant is filled in from the actual confidence
score at runtime; the uncertain variant deliberately omits a number because a
mid-range percentage misleads more than it informs.)

---

## Appeals Workflow

- **Who can appeal:** the creator of the content. They identify the item by the
  `content_id` returned at submission. (A production system would authenticate
  this against `creator_id`; here identification by `content_id` is sufficient.)
- **What they provide:** `content_id` and a free-text `creator_reasoning`
  explaining why they believe the classification is wrong.
- **What the system does on receipt:**
  1. Updates the content's status from `classified` to `under_review`.
  2. Appends an appeal entry to the audit log, stored alongside the original
     decision (original attribution, confidence, and both signal scores remain
     visible).
  3. Returns a confirmation `{content_id, status, message}`.
- **What a human reviewer sees:** the appeal queue (items with status
  `under_review`) showing, per item: `content_id`, original `attribution`,
  `confidence`, `llm_score`, `stylo_score`, the `creator_reasoning`, and
  timestamps for both the original decision and the appeal.
- **Out of scope:** automated re-classification. The appeal flags the item for
  human review; it does not re-run detection.

---

## Anticipated Edge Cases

1. **Non-native formal English by a genuine human author.** A real creator
   writing in careful, uniform, formal English. Signal 1 reads it as "too
   smooth" and Signal 2 reads the uniform sentence lengths as AI-like — *both
   signals fail in the same direction*, so the veto (which only fires on signal
   *disagreement*) cannot save it. This is the worst-case false positive and is
   handled only by the appeal path, not by the scoring logic. This is a known
   limitation, documented honestly rather than hidden.

2. **Poetry / lyrics with heavy repetition and simple vocabulary.** Repetitive,
   short-lined verse has low sentence-length variation and low type–token
   ratio, so the stylometric signal scores it AI-like. The LLM signal, however,
   often reads creative/figurative language as human. Because the two signals
   *disagree*, the veto fires and the item lands in "uncertain" rather than a
   wrongful AI label — an intended demonstration of the asymmetric design
   protecting a human creator.

---

## AI Tool Plan

For each implementation milestone: which spec sections feed the AI tool, what is
requested, and how the output is verified before it is wired in.

### M3 — Submission endpoint + first signal
- **Input to AI tool:** Detection Signals section (Signal 1) + the Architecture
  diagram.
- **Request:** Flask app skeleton with a `POST /submit` route stub, plus the
  `llm_signal(text)` function returning a 0–1 score.
- **Verify:** call `llm_signal` directly on a few inputs and confirm it returns
  a 0–1 float; confirm the route shape matches the API contract before wiring.

### M4 — Second signal + confidence scoring
- **Input to AI tool:** Detection Signals (Signal 2) + Uncertainty
  Representation + the Architecture diagram.
- **Request:** `stylometric_signal(text)` function and the `score()` function
  implementing the weighted-average + veto + band logic exactly as specified.
- **Verify:** confirm the generated thresholds match the table above (AI tools
  often drift from specified ranges); run the four labeled test inputs and
  confirm they land in distinct bands.

### M5 — Production layer
- **Input to AI tool:** Transparency Label Variants + Appeals Workflow + the
  Architecture diagram.
- **Request:** a `generate_label(attribution, confidence)` function and the
  `POST /appeal` endpoint.
- **Verify:** ask the tool to print all three label variants and confirm the
  text matches this spec; confirm an appeal updates status to `under_review` and
  appends to the log before considering it done.