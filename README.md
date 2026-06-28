# Provenance Guard

A backend that a creative-sharing platform can plug in to estimate whether
submitted text was written by a human or generated with AI, score its
confidence honestly, surface a plain-language transparency label to readers,
and let creators appeal a classification.

The goal is **not** perfect AI detection (an unsolved problem) but *responsible*
handling of an uncertain judgment: communicate uncertainty, avoid wrongly
accusing real creators, and provide a path to contest a decision.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows (Git Bash): source .venv/Scripts/activate
pip install -r requirements.txt
```

Create a `.env` in the repo root (it is gitignored):

```
GROQ_API_KEY=your_key_here
```

Run:

```bash
python app.py        # serves on http://localhost:5000
```

Endpoints: `POST /submit`, `POST /appeal`, `GET /log`.

---

## Architecture overview

A submission enters at `POST /submit` (`{text, creator_id}`) and is assigned a
unique `content_id`. The text is scored independently by two signals — an LLM
classifier (semantic) and stylometric heuristics (structural). The confidence
scorer combines those two scores into one calibrated 0–1 value, the label
generator maps that value to one of three plain-language variants, every value
is written to the SQLite audit log, and a structured JSON response is returned
(`content_id`, `attribution`, `confidence`, `label`, and both signal scores).

A creator who disputes a result calls `POST /appeal` (`{content_id,
creator_reasoning}`). The original decision's status flips from `classified` to
`under_review`, the appeal is appended to the audit log alongside the original
decision, and a confirmation is returned. Re-classification is not automated;
the appeal flags the item for human review.

```
POST /submit ─► Signal 1 (LLM) ─┐
                Signal 2 (stylo)─┤─► Confidence Scorer ─► Label Generator ─► Audit Log ─► JSON response
POST /appeal ─► status: classified → under_review ─► Audit Log (append)   ─► confirmation
```

---

## Detection signals

Two **distinct** signals — one semantic, one structural — so the combination is
more informative than either alone.

**Signal 1 — LLM classification (Groq, llama-3.3-70b-versatile).** Prompts the
model to estimate how AI-generated the text reads and return a 0–100 score
(normalized to 0–1). It captures *holistic* semantic and stylistic coherence:
human writing tends to make small logical jumps and include idiosyncratic
detail, while AI text is smoother and more uniform. **What it misses:** it is
weak on very short text, can fail to recognize AI text, and shifts with prompt
wording.

**Signal 2 — Stylometric heuristics (pure Python).** Measures statistical
regularity: burstiness (coefficient of variation of sentence length),
type–token ratio (vocabulary diversity), and punctuation density. AI text tends
to be uniform; human writing varies. This is a genuinely independent axis from
Signal 1 — it reads *structure*, not meaning. **What it misses:** formal,
uniform human writing (academic prose, non-native formal English) scores
AI-like, and short text starves the metrics (see Known limitations).

---

## Confidence scoring

`confidence` is a single 0–1 score = AI-likelihood. `0` = confidently human,
`1` = confidently AI, `0.5` = genuinely undecidable.

Signals combine as `combined = 0.75 * llm_score + 0.25 * stylo_score`. The LLM
is weighted higher because it is the more reliable signal, especially on short
text. The mapping is **asymmetric** to protect human creators (a false "AI"
label is worse than a missed one):

| combined score | condition | attribution |
|---|---|---|
| ≥ 0.70 | **and** llm_score ≥ 0.45 | likely_ai |
| ≤ 0.40 | — | likely_human |
| otherwise | — | uncertain |

The AI threshold (0.70) sits well above the midpoint while the human threshold
(0.40) sits near it, so borderline cases fall toward *uncertain* / *human*. AI
verdicts additionally require the LLM to concur, so the weaker stylometric
signal can refine confidence but cannot single-handedly trigger an AI label.

**Validation that scores are meaningful.** Tested with the four labeled inputs
from the spec (clearly AI, clearly human, formal-human borderline,
lightly-edited-AI borderline) and confirmed they land in different bands. Two
real runs:

| input | llm_score | stylo_score | confidence | attribution |
|---|---|---|---|---|
| Clearly AI (formal essay) | 0.90 | 0.333 | **0.758** | likely_ai |
| Clearly human (casual review) | 0.20 | 0.0 | **0.15** | likely_human |

A confidence of 0.758 and 0.15 produce visibly different labels, confirming the
scoring is not a binary flip at 0.5.

---

## Transparency label

Three variants, distinguished by **wording**, not just a number. High-confidence
labels still avoid absolute claims and point to the appeal path.

| Variant | Text |
|---|---|
| **High-confidence AI** | "This content shows strong signs of being AI-generated (estimated 76% likely). This is an automated guess and can be wrong — if you wrote it yourself, you can request a review." |
| **High-confidence human** | "This content appears to be human-written. Our analysis found no strong signs of AI generation. This is an automated estimate, not a guarantee." |
| **Uncertain** | "We couldn't confidently tell whether this was written by a person or with AI help — the signals were mixed. We're flagging it as uncertain rather than guessing." |

The percentage in the AI variant is filled from the live confidence score; the
uncertain variant deliberately omits a number, since a mid-range percentage
misleads more than it informs.

---

## Appeals workflow

The creator identifies content by `content_id` and submits free-text
`creator_reasoning`. On receipt the system flips the original decision's status
to `under_review`, appends an appeal entry to the audit log carrying the
original decision alongside it, and returns a confirmation. A human reviewer
sees the appeal queue with the original attribution, confidence, both signal
scores, the reasoning, and timestamps. Automated re-classification is out of
scope.

---

## Rate limiting

`10 per minute; 100 per day` on `POST /submit` (Flask-Limiter, in-memory store).
A real creator never submits their own work dozens of times a minute, so normal
use never hits the limit, and 100/day covers even a prolific writer. A script
flooding the endpoint with hundreds of requests a minute is stopped at 10/min.
The values block automated abuse without touching legitimate use.

Observed when firing 12 requests in a loop:

```
200 200 200 200 200 200 200 200 200 200 429 429
```

---

## Audit log

SQLite, structured. Every decision records timestamp, content_id, creator_id,
attribution, confidence, both signal scores, and status; appeals add the
reasoning and flip status to `under_review`. Sample (`GET /log`):

```json
{"event_type": "appeal",     "content_id": "9fc5d813…", "attribution": "likely_ai",    "confidence": 0.758, "llm_score": 0.9, "stylo_score": 0.333, "status": "under_review", "appeal_reasoning": "I wrote this myself… I am a non-native English speaker…"}
{"event_type": "submission", "content_id": "9fc5d813…", "attribution": "likely_ai",    "confidence": 0.758, "llm_score": 0.9, "stylo_score": 0.333, "status": "under_review", "appeal_reasoning": null}
{"event_type": "submission", "content_id": "eb9cb261…", "attribution": "likely_human", "confidence": 0.15,  "llm_score": 0.2, "stylo_score": 0.0,   "status": "classified",   "appeal_reasoning": null}
```

The appeal entry sits alongside the original submission entry, and the original
entry's status has flipped to `under_review`.

---

## Known limitations

**Non-native formal English by a genuine human author.** A real creator writing
in careful, uniform, formal English is the system's worst case. The LLM signal
reads it as "too smooth" and the stylometric signal reads its uniform sentence
lengths as AI-like — *both signals fail in the same direction.* Because they
agree, no internal guard catches it (the design only blocks AI verdicts when the
LLM disagrees). This is handled only by the appeal path, not by the scoring
logic. It is a direct consequence of what the two signals measure, and is the
reason the appeal workflow exists.

A second weak spot: very short text starves the stylometric metrics
(type–token ratio is near-meaningless under ~4 sentences), so on short
submissions the system leans almost entirely on the LLM signal.

---

## Spec reflection

**Where the spec helped.** Writing the confidence-scoring section in planning.md
*before* coding forced a decision on what 0.5 means to a user and where the
label thresholds sit. That pre-work meant the scoring code was implementing a
defined contract rather than being invented at the keyboard.

**Where implementation diverged.** The plan specified equal weighting
(`0.5 llm + 0.5 stylo`) and a symmetric veto (`if min(signal) < 0.35 →
uncertain`). In M4 testing this proved broken: clearly-AI text scored only 0.26
on stylometry (short text, dead TTR), so the veto fired and `likely_ai` became
unreachable — the system could never call anything AI. I reweighted to
`0.75/0.25` and moved the deciding power to the LLM (AI verdicts now require
`combined ≥ 0.70 and llm ≥ 0.45`). The original intent — "when in doubt, side
with the human" — is preserved; the veto power just moved from the weak signal
to the trustworthy one.

---

## AI usage

**1 — Confidence scoring (M4).** I gave the AI tool the detection-signals and
uncertainty sections of planning.md plus the architecture diagram and asked it
to generate the stylometric signal function and the combination logic. It
implemented the `0.5/0.5` average and `min(signal) < 0.35` veto exactly as
specced. Testing the four labeled inputs revealed the veto made `likely_ai`
unreachable (clearly-AI text scored 0.26 on stylometry). I overrode the
generated logic: reweighted to `0.75/0.25` and replaced the veto with an
LLM-agreement gate, keeping the asymmetric intent but making AI verdicts
actually reachable.

**2 — Flask skeleton + first signal (M3).** I gave the AI tool the detection-
signals section and the diagram and asked for the Flask app skeleton plus the
`llm_signal` function. The generated code ran, but I made two changes before
using it: I standardized every signal to return 0 = human / 1 = AI (the draft
was ambiguous about direction), and I added code-fence stripping to the JSON
parsing because Groq occasionally wraps its reply in ```` ```json ````, which
would otherwise crash `json.loads`.