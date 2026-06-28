"""Signal 2 (stylometric) + confidence scoring for Provenance Guard.

stylometric_signal(text) -> 0-1 (1 = strongly AI), pure Python.
score(llm_score, stylo_score) -> (combined, attribution) using the
asymmetric weighted-average + veto + band logic from planning.md.
"""
import re


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _sentences(text):
    return [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]


def _words(text):
    return re.findall(r"\b[\w']+\b", text.lower())


def stylometric_signal(text, debug=False):
    """Signal 2 — structural. Returns 0-1 (1 = strongly AI)."""
    sents = _sentences(text)
    words = _words(text)

    # Too short to judge structure -> stay neutral (pushes toward 'uncertain')
    if len(sents) < 2 or len(words) < 12:
        return (0.5, {"note": "too short"}) if debug else 0.5

    # 1) Burstiness — coefficient of variation of sentence length.
    #    Low variation -> uniform -> AI-like.
    lengths = [len(_words(s)) for s in sents]
    mean_len = sum(lengths) / len(lengths)
    std = (sum((l - mean_len) ** 2 for l in lengths) / len(lengths)) ** 0.5
    cv = (std / mean_len) if mean_len else 0.0
    burst_ai = _clamp((0.55 - cv) / (0.55 - 0.20))

    # 2) Type-token ratio — vocabulary diversity. Low TTR -> repetitive -> AI-like.
    ttr = len(set(words)) / len(words)
    ttr_ai = _clamp((0.72 - ttr) / (0.72 - 0.45))

    # 3) Punctuation density — commas/semicolons/colons per word.
    #    Dense, formulaic punctuation -> weakly AI-like.
    puncts = re.findall(r"[,;:]", text)
    pdens = len(puncts) / len(words)
    punct_ai = _clamp((pdens - 0.04) / (0.14 - 0.04))

    # Weighted blend — burstiness is the strongest signal, punctuation weakest.
    stylo = 0.5 * burst_ai + 0.3 * ttr_ai + 0.2 * punct_ai

    if debug:
        return stylo, {
            "cv": round(cv, 3), "burst_ai": round(burst_ai, 2),
            "ttr": round(ttr, 3), "ttr_ai": round(ttr_ai, 2),
            "pdens": round(pdens, 3), "punct_ai": round(punct_ai, 2),
            "stylo": round(stylo, 3),
        }
    return stylo


def score(llm_score, stylo_score):
    """Asymmetric combination. Returns (combined_confidence, attribution).

    The LLM is the more reliable signal (especially on short text, where
    stylometry is weak), so it is weighted higher AND given the deciding vote
    on AI verdicts: stylometry can refine confidence but cannot single-handedly
    trigger a 'likely_ai' call. The thresholds stay asymmetric -- the AI bar
    (0.70) sits well above the midpoint while the human bar (0.40) sits near it
    -- so borderline cases fall toward 'uncertain'/'human', never a wrongful AI
    label. Spirit of the original veto is kept ('when in doubt, side with the
    human'); the deciding power just moved to the trustworthy signal.
    """
    combined = 0.75 * llm_score + 0.25 * stylo_score

    if combined >= 0.70 and llm_score >= 0.45:
        attribution = "likely_ai"       # high bar + LLM must concur
    elif combined <= 0.40:
        attribution = "likely_human"    # easier to call human
    else:
        attribution = "uncertain"       # when in doubt, don't accuse

    return round(combined, 3), attribution


def generate_label(attribution, confidence):
    """Map an attribution + confidence to plain-language reader-facing text.

    Three variants, distinguished by wording (not just a number). The AI
    variant fills in the live percentage and points to the appeal path; the
    uncertain variant deliberately omits a number (a mid-range % misleads).
    """
    if attribution == "likely_ai":
        pct = round(confidence * 100)
        return (
            f"This content shows strong signs of being AI-generated "
            f"(estimated {pct}% likely). This is an automated guess and can be "
            f"wrong — if you wrote it yourself, you can request a review."
        )
    if attribution == "likely_human":
        return (
            "This content appears to be human-written. Our analysis found no "
            "strong signs of AI generation. This is an automated estimate, not "
            "a guarantee."
        )
    return (
        "We couldn't confidently tell whether this was written by a person or "
        "with AI help — the signals were mixed. We're flagging it as uncertain "
        "rather than guessing."
    )