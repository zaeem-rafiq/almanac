"""Drafts the "📌 Update" note for a video whose claims went stale.

RULE: one of only two modules permitted to call a model (the other is extract.py).
It WRITES PROSE from a verdict judge.py already decided. It never decides.

The division of labour, restated because this is the module where it is easiest to lose:

  judge.py   decided the status, from rules, and the verdict already carries `rule_fired`
  the model  is handed that finished verdict and asked for one viewer-facing sentence
  the code   below refuses the sentence if it drops the number or starts giving advice

`check_note()` is a CODE assertion, not a prompt instruction. A prompt that says "do not give
advice" is a request; a function that rejects the string is a guarantee. The model gets two
attempts, with its violations fed back, and then a deterministic template ships instead — and the
verdict records `note_source="fallback"` so a run where that happened is visible in the artifact
rather than indistinguishable from one where the model succeeded.
"""

from __future__ import annotations

import re

from almanac.judge import Verdict

MAX_NOTE_CHARS = 280
MAX_ATTEMPTS = 2
MAX_TOKENS = 512

# A note describes what changed. The moment it tells a viewer what to do with the change, Almanac
# is dispensing financial advice under a creator's name, which it must never do. Inflections are
# listed explicitly: `\bbuy\b` does not match "buying", and "buying" is the same instruction.
ADVICE_VERBS = (
    "should", "shouldn't", "should've",
    "must", "mustn't",
    "buy", "buys", "buying",
    "sell", "sells", "selling",
)
_ADVICE_RE = re.compile(r"\b(" + "|".join(re.escape(v) for v in ADVICE_VERBS) + r")\b", re.I)

SYSTEM_PROMPT = """You write one short correction note that a personal-finance creator will pin \
under an older video of theirs. The checking has already been done by a rules engine; you are not \
checking anything and you are not deciding anything. You are writing one sentence.

Constraints, all of them hard:
- At most 280 characters, including the pin emoji.
- It MUST contain this exact string, character for character: {value_string}
- It MUST contain the year {year}.
- It must NOT tell the viewer what to do. No "should", no "must", no "buy", no "sell". State what
  the number is now and what the video said. Nothing else.
- Plain, calm, factual. The creator's own voice: no hype, no apology, no exclamation marks.
- Start with the pin emoji.

Reply with the note itself and nothing else — no preamble, no quotation marks around it."""


def format_value(value: float | None, unit: str | None) -> str:
    """Render a number the way a viewer reads it: `$24,500` or `6.71%`.

    This is the string `check_note()` requires the note to contain, so it is defined once and used
    both to instruct the model and to judge its answer. If those two ever diverge, the assertion
    stops meaning anything.
    """
    if value is None:
        return ""
    if unit == "usd":
        return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"
    if unit == "pct":
        return f"{value:.2f}%".replace(".00%", "%")
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def required_strings(verdict: Verdict) -> tuple[str, str]:
    """The two substrings a note must carry: the formatted current value, and its effective year.

    Both are read off the Verdict `judge.py` already produced — `expected` is the current value the
    rule compared against, and `fact.as_of` is the date that value took effect. Nothing here
    recomputes a decision; if the verdict did not resolve a fact, `check_note` refuses rather than
    inventing one.
    """
    unit = verdict.claim.unit or _catalog_unit(verdict)
    value_string = format_value(verdict.expected, unit)
    as_of = verdict.fact.as_of if verdict.fact else None
    return value_string, (str(as_of.year) if as_of else "")


def check_note(note: str, verdict: Verdict) -> list[str]:
    """Return every reason this note is unacceptable. Empty list means it ships.

    Called on the model's output AND on the fallback, so nothing reaches a report unchecked.
    """
    problems: list[str] = []
    text = (note or "").strip()
    if not text:
        return ["note is empty"]
    if len(text) > MAX_NOTE_CHARS:
        problems.append(f"note is {len(text)} chars, limit is {MAX_NOTE_CHARS}")

    value_string, year = required_strings(verdict)
    if not value_string:
        problems.append("verdict carries no current value to state")
    elif value_string not in text:
        problems.append(f"note does not contain the current value {value_string!r}")
    if not year:
        problems.append("verdict carries no effective date")
    elif year not in text:
        problems.append(f"note does not contain the effective year {year!r}")

    found = sorted({m.group(0).lower() for m in _ADVICE_RE.finditer(text)})
    if found:
        problems.append(f"note gives advice: {', '.join(found)}")
    return problems


def fallback_note(verdict: Verdict) -> str:
    """A deterministic note built from the verdict alone, used when the model's cannot pass.

    Constructed to satisfy `check_note()` by construction — and still passed through it by the
    caller, because "by construction" is a claim and the assertion is the proof.
    """
    value_string, _ = required_strings(verdict)
    said = format_value(verdict.claim.value, verdict.claim.unit or _catalog_unit(verdict))
    on = verdict.fact.as_of.isoformat() if verdict.fact and verdict.fact.as_of else ""
    return (
        f"📌 Update: this video says {said}. "
        f"The current figure is {value_string}, effective {on}."
    )[:MAX_NOTE_CHARS]


def draft_note(verdict: Verdict, client=None, model: str | None = None) -> tuple[str, str]:
    """Ask the model to phrase this already-decided verdict. Returns `(note, source)`.

    Raises nothing on a bad model answer — it retries with the violations named, then falls back.
    """
    value_string, year = required_strings(verdict)
    if client is None:
        # Same construction extract.py uses: one place builds the Vertex client and model id.
        from almanac.extract import _client

        client, default_model = _client()
        model = model or default_model

    system = SYSTEM_PROMPT.format(value_string=value_string, year=year)
    messages = [{"role": "user", "content": _brief(verdict)}]

    from google.genai import types

    # Vertex AI, same client extract.py builds. Note drafting is generative rather than a
    # labelling task, so it does NOT pin temperature the way extraction does — but the code
    # assertion in `check_note` is what actually governs the output, not the sampling.
    for _ in range(MAX_ATTEMPTS):
        response = client.models.generate_content(
            model=model,
            contents=[{"role": m["role"], "parts": [{"text": m["content"]}]} for m in messages],
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=MAX_TOKENS,
            ),
        )
        note = (getattr(response, "text", None) or "").strip()
        problems = check_note(note, verdict)
        if not problems:
            return note, "model"
        # Gemini expects "model" where the Anthropic shape used "assistant".
        messages = messages + [
            {"role": "model", "content": note or "(empty)"},
            {"role": "user", "content": "That note was rejected: " + "; ".join(problems)
             + ". Write it again, fixing exactly those problems."},
        ]

    return fallback_note(verdict), "fallback"


def annotate(
    verdicts: list[Verdict], client=None, model: str | None = None
) -> dict[tuple[str, str], tuple[str, str]]:
    """Draft a note for every `stale_material` verdict, and for no other status.

    Returns `{(source_id, locator): (note, note_source)}` rather than a mutated verdict list.
    `judge.Verdict` is a frozen dataclass owned by judge.py and it carries no note field — that is
    the right shape, because a note is not part of a decision. The report joins the two.

    A note is a correction. `correct`, `stale_immaterial`, `skip` and `unresolved` have nothing to
    correct, so they get none — writing prose about them would invent a problem the rules did not
    find.
    """
    notes: dict[tuple[str, str], tuple[str, str]] = {}
    for verdict in verdicts:
        if verdict.status != "stale_material":
            continue
        note, source = draft_note(verdict, client=client, model=model)
        if check_note(note, verdict):  # the fallback is checked too; nothing ships unchecked
            note, source = fallback_note(verdict), "fallback"
        notes[(verdict.claim.source_id, verdict.claim.locator)] = (note, source)
    return notes


def _brief(verdict: Verdict) -> str:
    """What the model is told. A finished verdict, never a question about one."""
    value_string, _ = required_strings(verdict)
    said = format_value(verdict.claim.value, verdict.claim.unit or _catalog_unit(verdict))
    return (
        f"The video said: \"{verdict.claim.quote}\"\n"
        f"That number was {said}. The current figure is {value_string}, "
        f"effective {verdict.fact.as_of if verdict.fact else None}.\n"
        f"Write the note."
    )


def _catalog_unit(verdict: Verdict) -> str | None:
    """Fall back to the catalog's unit when the claim did not carry one."""
    if verdict.fact is not None:
        return verdict.fact.unit
    if not verdict.claim.entity_key:
        return None
    from almanac.catalog import load_catalog

    entry = load_catalog().get(verdict.claim.entity_key)
    return entry.unit if entry else None
