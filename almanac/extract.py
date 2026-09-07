"""Pulls numeric claims out of scripts and captions.

RULE: one of only two modules permitted to call a model (the other is notes.py).
It READS. It never decides — no verdict is produced here.

The division of labour, which is the whole point of this module:

  the model  labels what a sentence IS  — statutory_limit / illustrative / historical / ...
  the code   decides what is admitted   — the catalog key, the locator, the verbatim quote

So the system prompt below contains no verdict vocabulary. It never asks whether a number is
current, right, or worth changing; `judge.py` answers that from rules, and every verdict it
emits carries a `rule_fired`. Moving any part of that question into this prompt would make the
decision unauditable, which is the one thing Almanac is built not to do.
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NamedTuple

from pydantic import BaseModel, Field, ValidationError

from almanac.models import Claim, ClaimType, Unit

REPO_ROOT = Path(__file__).resolve().parents[1]

MAX_QUOTE_CHARS = 200
CHARS_PER_TOKEN = 4
CHUNK_TOKENS = 2_000
CHUNK_CHARS = CHUNK_TOKENS * CHARS_PER_TOKEN
MAX_CONCURRENCY = 4
MAX_TOKENS = 4_096


class Locator(NamedTuple):
    """A labelled span of the source document, in character offsets.

    `label` is what ends up on `Claim.locator`: an SRT start timestamp, or `L<n>` for a script
    line. The span is what makes the label recoverable from a quote without asking the model.
    """

    start: int
    end: int
    label: str


@dataclass(frozen=True)
class Source:
    """A document flattened for extraction, with its locator spans intact."""

    source_id: str
    text: str
    locators: list[Locator]


# ------------------------------------------------------------------------------------ readers


def _relative_id(path: Path) -> str:
    """Render a repo-relative source id, matching the convention in evals/claims.jsonl."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def read_srt(path: str | os.PathLike[str]) -> Source:
    """Flatten an SRT into one document, joining cue texts with a single space.

    The single space matters: the labelled quotes in `evals/claims.jsonl` cross cue boundaries
    ("you can defer twenty three thousand dollars of your own money into your 401k" spans three
    cues), so any other join would make those quotes unfindable.
    """
    import srt

    path = Path(path)
    subtitles = list(srt.parse(path.read_text(encoding="utf-8")))

    parts: list[str] = []
    locators: list[Locator] = []
    cursor = 0
    for sub in subtitles:
        content = " ".join(sub.content.split())
        if not content:
            continue
        if parts:
            cursor += 1  # the joining space
        locators.append(Locator(cursor, cursor + len(content), _srt_timestamp(sub.start)))
        parts.append(content)
        cursor += len(content)

    return Source(_relative_id(path), " ".join(parts), locators)


def _srt_timestamp(delta) -> str:
    """Render a timedelta as an SRT `HH:MM:SS,mmm` start stamp."""
    total_ms = int(delta.total_seconds() * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def read_script(path: str | os.PathLike[str]) -> Source:
    """Flatten a markdown script, one locator per non-blank line, labelled `L<n>`.

    Line numbers are 1-based and count blank lines, so `L7` is the seventh line of the file as an
    editor shows it — otherwise the locator would not help a human find the sentence.
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8")

    parts: list[str] = []
    locators: list[Locator] = []
    cursor = 0
    for number, line in enumerate(raw.splitlines(), start=1):
        content = " ".join(line.split())
        if not content:
            continue
        if parts:
            cursor += 1
        locators.append(Locator(cursor, cursor + len(content), f"L{number}"))
        parts.append(content)
        cursor += len(content)

    return Source(_relative_id(path), " ".join(parts), locators)


def read_source(path: str | os.PathLike[str]) -> Source:
    """Read one `.srt` or `.md` file, dispatching on suffix."""
    path = Path(path)
    if path.suffix.lower() == ".srt":
        return read_srt(path)
    if path.suffix.lower() in {".md", ".markdown", ".txt"}:
        return read_script(path)
    raise ValueError(f"unsupported source type: {path}")


def discover_sources(target: str | os.PathLike[str]) -> list[Source]:
    """Resolve a file, a video directory, or a tree of them into readable sources."""
    path = Path(target)
    if path.is_file():
        return [read_source(path)]
    if not path.is_dir():
        raise FileNotFoundError(f"no such source: {target}")

    found = sorted(
        p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in {".srt", ".md", ".markdown"}
    )
    if not found:
        raise FileNotFoundError(f"no .srt or .md files under {target}")
    return [read_source(p) for p in found]


# ------------------------------------------------------------- verbatim recovery and locators

_CURLY = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    " ": " ",
}


def _normalise(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs and straighten curly quotes, keeping an index map back.

    Returns `(normalised, index_map)` where `index_map[i]` is the offset in `text` of the
    character that produced `normalised[i]`. The map is what lets a match found in normalised
    space be sliced out of the ORIGINAL string, so the quote we store is the source's own bytes.

    This is deliberately the *only* leniency in quote matching. Whitespace and smart quotes differ
    between what a model echoes back and what a caption file holds; a hallucinated sentence does
    not. Anything looser — a similarity ratio, a token-overlap score — would let an invented quote
    through, which is precisely what the verbatim proof exists to catch.
    """
    out: list[str] = []
    index_map: list[int] = []
    pending_space = False

    for i, char in enumerate(text):
        char = _CURLY.get(char, char)
        if char.isspace():
            pending_space = bool(out)
            continue
        if pending_space:
            out.append(" ")
            index_map.append(i)
            pending_space = False
        out.append(char)
        index_map.append(i)

    return "".join(out), index_map


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """Find `quote` in `text` under whitespace/curly-quote normalisation only.

    Returns original-string `(start, end)` offsets, or `None` when the quote is not present —
    which is the signal that the model invented it.
    """
    if not quote or not quote.strip():
        return None

    normal_text, index_map = _normalise(text)
    normal_quote, _ = _normalise(quote)
    if not normal_quote:
        return None

    position = normal_text.find(normal_quote)
    if position < 0:
        return None

    start = index_map[position]
    end = index_map[position + len(normal_quote) - 1] + 1
    return start, end


def _trim_to_limit(text: str, start: int, end: int) -> tuple[int, int]:
    """Shorten an over-long span to the last word boundary within MAX_QUOTE_CHARS.

    A prefix of a verbatim substring is still a verbatim substring, so trimming cannot make a
    quote unfaithful — it only makes it displayable.
    """
    if end - start <= MAX_QUOTE_CHARS:
        return start, end
    window = text[start : start + MAX_QUOTE_CHARS]
    cut = window.rfind(" ")
    return start, start + (cut if cut > 0 else MAX_QUOTE_CHARS)


def locator_for(locators: Iterable[Locator], offset: int) -> str:
    """Pick the label of the span containing `offset`, else the nearest preceding span."""
    best = ""
    for locator in locators:
        if locator.start <= offset < locator.end:
            return locator.label
        if locator.start <= offset:
            best = locator.label
    return best


# ---------------------------------------------------------------------------------- chunking


def chunk(source: Source, chunk_chars: int = CHUNK_CHARS) -> list[tuple[int, str]]:
    """Split a source into `(base_offset, text)` chunks cut on locator boundaries.

    Cutting on locator boundaries is what "locators preserved" means mechanically: a quote found
    at chunk-local offset `i` is at global offset `base + i`, so the locator lookup is exact
    rather than approximate. Cutting mid-cue would put quotes in a chunk whose offsets no longer
    line up with the document.
    """
    if not source.locators:
        return [(0, source.text)] if source.text.strip() else []

    chunks: list[tuple[int, str]] = []
    base = source.locators[0].start
    end = base
    for locator in source.locators:
        if locator.end - base > chunk_chars and end > base:
            chunks.append((base, source.text[base:end]))
            base = locator.start
        end = locator.end
    if end > base:
        chunks.append((base, source.text[base:end]))
    return chunks


# ------------------------------------------------------------------------- the model-facing IO


class ExtractedClaim(BaseModel):
    """Exactly what the model is asked for — and nothing more.

    `source_id` and `locator` are absent on purpose. The model is never given the chance to
    assert where a sentence came from, so it cannot get that wrong.
    """

    quote: str = Field(
        description="The sentence, copied WORD FOR WORD from the text. Never paraphrase, "
        "never tidy the grammar, never add or drop a word. Keep it under 200 characters."
    )
    claim_type: ClaimType = Field(description="Which of the six labels this sentence is.")
    entity_key: str | None = Field(
        default=None,
        description="The key from the closed vocabulary that this sentence names, or null if "
        "no key in the vocabulary is the thing being talked about.",
    )
    value: float | None = Field(
        default=None,
        description="The number as digits. Numbers spelled out in words become digits: "
        "'twenty three thousand dollars' -> 23000, 'nine point six two percent' -> 9.62. "
        "Null if the sentence carries no number.",
    )
    unit: Unit | None = Field(default=None, description="'usd' for dollars, 'pct' for percent, else null.")
    year_hint: int | None = Field(
        default=None, description="The year the sentence itself names, e.g. 2024. Null if it names none."
    )
    confidence: float = Field(description="0 to 1: how sure you are of this labelling.")


class Extraction(BaseModel):
    """The tool payload. Nesting is intentional — ADR-000 G-3 proved `$defs`/`$ref` is accepted."""

    claims: list[ExtractedClaim]


TOOL_NAME = "record_claims"

SYSTEM_PROMPT = """You are a careful reader working through a personal-finance video transcript \
or script. Your one job is to find every sentence that contains a number and label WHAT KIND OF \
SENTENCE IT IS. You are not evaluating anything. You are not checking anything against the world. \
Another system does that, from rules, later. Label the sentence and move on.

## The six labels

statutory_limit - a number set by law or by an agency: contribution limits, catch-up amounts,
  wage bases, standard deductions, exclusion amounts, thresholds written into the tax code.
tax_bracket     - a marginal tax rate or bracket the speaker attributes to a filer.
market_rate     - a rate the market or an issuer sets and that moves on its own: mortgage
  averages, the federal funds rate, treasury yields, bond composite rates, inflation readings.
illustrative    - a number the speaker made up to carry an example. The giveaway is framing:
  "say you", "let us say", "suppose", "imagine", "assume", "for the sake of the math", or a
  round hypothetical salary, balance, or monthly contribution.
historical      - a number the speaker explicitly places in the past. The giveaway is a past-tense
  frame plus a time marker: "back in 2022 it paid", "it used to be", "last year it was".
structural      - a rule about HOW a product or account works, rather than a value it currently
  carries: lockup periods, holding periods, when a rate resets, redemption penalties expressed in
  time, age thresholds that change how the account behaves, eligibility conditions. These are the
  mechanics of the instrument. They are not set by a contribution table and they do not move with
  the market: "you cannot touch the money for twelve months", "it resets on the first of May and
  the first of November", "if you cash out before five years you give up three months of interest",
  "non medical withdrawals before sixty five get taxed and penalized".
other           - a sentence with a number that is none of the above: step numbers, fund counts,
  and any other number you cannot place in one of the six labels above.

## The distinction that matters most

Three sentences can carry the same number and be three different things:

  "the 401(k) limit is $23,000"     -> statutory_limit   (asserted as the rule)
  "say you contribute $23,000"      -> illustrative      (invented to carry an example)
  "in 2022 it paid 9.62%"           -> historical        (placed in the past by the speaker)

Read the framing around the number, not the number. A big round figure inside "say you earn ..."
is illustrative no matter how plausible it is. A figure the speaker dates to a past year is
historical even when it is a real statutory or market figure.

Framing beats subject matter, always. "Say you are in the twenty two percent bracket" is
illustrative, not tax_bracket - the speaker invented that filer to carry an example. "Say you
have three hundred thousand dollars left on the loan" is illustrative, not a market_rate or a
limit. Ask first "is the speaker asserting this, or supposing it, or remembering it", and only
then ask what the number is about.

## entity_key - a CLOSED vocabulary

Set entity_key ONLY to one of the keys listed below, and only when the sentence is talking about
that exact thing. If nothing in the list is what the sentence is about, use null. Do not invent a
key, do not adapt a key, do not pick the closest one. null is an ordinary and common answer -
most sentences with numbers in them are not about any of these.

{vocabulary}

## Rules for every claim you record

- quote must be copied WORD FOR WORD from the text you are given. Do not paraphrase, do not
  tidy the grammar, do not merge two distant sentences. Keep it under 200 characters. If you
  cannot copy it exactly, do not record the claim.
- value is the number as digits. This text spells numbers out in words, so convert:
  "twenty three thousand dollars" -> 23000, "six point eight five percent" -> 6.85,
  "eight thousand five hundred fifty dollars" -> 8550.
- unit is "usd" for dollar amounts, "pct" for percentages, null otherwise.
- year_hint is the year the sentence itself names, and null when it names none.
- One claim per sentence-with-a-number. Record every one you find.
"""


def _vocabulary_block() -> str:
    """Render the catalog keys WITH LABELS, read from facts/catalog.yaml at runtime.

    Read, not hardcoded: the closed vocabulary is whatever the catalog holds, so adding a key to
    facts/catalog.yaml is enough to make the extractor aware of it.
    """
    from almanac.catalog import load_catalog

    lines = []
    for key, entry in load_catalog().items():
        lines.append(f"  {key}  ({entry.kind}, {entry.unit}) - {entry.label}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    return SYSTEM_PROMPT.format(vocabulary=_vocabulary_block())


def _admit_entity_key(proposed: str | None, allowed: set[str]) -> str | None:
    """The code, not the model, decides whether a key is real. Anything else becomes None."""
    if proposed is None:
        return None
    candidate = proposed.strip()
    return candidate if candidate in allowed else None


# ------------------------------------------------------------------------------- the model call


def _client():
    import anthropic

    from almanac.cli import load_env

    load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — see .env")
    return anthropic.Anthropic(api_key=api_key), os.environ.get("LLM_MODEL", "claude-opus-5")


# --- Cost controls -----------------------------------------------------------------------------
#
# Three levers, applied on Zaeem's instruction 2026-09-07 after the account ran out of credit.
# None of them changes WHAT the model is asked. The prompt, the tool schema, the forced tool
# choice and the parsing are byte-identical to what shipped; only the billing changes.
#
# 1. PROMPT CACHING. Every call re-sent the same ~1,900-token prefix at full price — the system
#    prompt (1,358 tok) + the tool schema (538 tok) — on every call of every run ever made. The
#    breakpoint sits at the END of `system`, so the cached prefix is tools + system (render order
#    is tools -> system -> messages) and the per-chunk text falls after it. Opus 5's minimum
#    cacheable prefix is 512 tokens, so this prefix qualifies.
#
#    NOT the top-level `cache_control` auto-breakpoint: that caches the LAST cacheable block,
#    which here is the chunk text. Every chunk differs, so each call would write a fresh entry
#    and none would ever read one — paying the 1.25x write premium for nothing.
#
# 2. EFFORT. Opus 5 runs adaptive thinking BY DEFAULT at effort `high` when `thinking` is omitted
#    (unlike Opus 4.7/4.8), and thinking bills as OUTPUT at 5x the input rate. Extraction is a
#    labelling task that was silently paying for deep reasoning nobody asked for.
#
#    Thinking is deliberately NOT disabled. On Opus 5, `thinking: {type: "disabled"}` can make the
#    model write a tool call into visible TEXT instead of a tool_use block — the turn succeeds, the
#    call never runs, and nothing raises. For a forced-tool-use extractor that failure is silent
#    and total, so the cheaper-and-safe lever is low effort, not no thinking.
#
# 3. BATCH. Opt-in via `scan --batch`: 50% off, asynchronous. Never the default and never used by
#    `lint` or the web endpoint, which have to answer while a human waits.
#
# UNVERIFIED AGAINST THE LIVE API. The credit balance is exhausted, so none of this has been
# measured end to end — `count_tokens` is refused too. The request SHAPE is asserted offline in
# tests/test_extract_cost.py; the savings are arithmetic, not observation. Confirm with
# `usage.cache_read_input_tokens` on the first paid run.

EXTRACT_EFFORT = "low"
CACHE_CONTROL = {"type": "ephemeral"}   # 5-minute TTL; a read refreshes the timer for free
BATCH_POLL_SECONDS = 5
BATCH_TIMEOUT_SECONDS = 3_600


def _request_params(chunk_text: str, system_prompt: str, model: str) -> dict:
    """The exact request body, built in ONE place.

    The synchronous path and the batch path both build their request here, so a batched scan
    cannot drift from an interactive one: the two differ in transport and price, never in what
    the model is asked.
    """
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        # A list of blocks, not a bare string: `cache_control` is a content-block field, and this
        # block is the last thing in the cached prefix.
        "system": [{
            "type": "text",
            "text": system_prompt,
            "cache_control": dict(CACHE_CONTROL),
        }],
        "output_config": {"effort": EXTRACT_EFFORT},
        "tools": [{
            "name": TOOL_NAME,
            "description": "Record every sentence that contains a number, with its label.",
            "input_schema": Extraction.model_json_schema(),
        }],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        "messages": [{"role": "user", "content": chunk_text}],
    }


def _claims_from_message(message) -> list[ExtractedClaim]:
    """Read the tool-use block. Unchanged behaviour; lifted out so the batch path shares it."""
    blocks = [b for b in message.content if getattr(b, "type", None) == "tool_use"]
    if not blocks:
        return []
    try:
        return Extraction.model_validate(blocks[0].input).claims
    except ValidationError:
        return []


def _call_model(chunk_text: str, system_prompt: str, client, model: str) -> list[ExtractedClaim]:
    """One tool-use round trip. Call shape verified in ADR-000 §4 / scripts/preflight.py."""
    return _claims_from_message(
        client.messages.create(**_request_params(chunk_text, system_prompt, model))
    )


def _call_model_batch(
    chunk_texts: dict[str, str],
    system_prompt: str,
    client,
    model: str,
    poll_seconds: float = BATCH_POLL_SECONDS,
    timeout_seconds: float = BATCH_TIMEOUT_SECONDS,
) -> dict[str, list[ExtractedClaim]]:
    """Run every extraction call as one Message Batch — 50% off, asynchronous.

    Results come back in ANY order, so they are keyed by `custom_id` and never by position. A
    request that errored yields no claims for its key rather than failing the whole run: the
    coverage sweep already exists to notice sentences nothing was produced for.
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    if not chunk_texts:
        return {}

    # The caller's keys are internal job ids, but custom_id has to survive the round trip, so the
    # mapping is explicit rather than assuming the keys are id-safe.
    ids = {f"job{index}": key for index, key in enumerate(chunk_texts)}
    batch = client.messages.batches.create(requests=[
        Request(
            custom_id=custom_id,
            params=MessageCreateParamsNonStreaming(
                **_request_params(chunk_texts[key], system_prompt, model)
            ),
        )
        for custom_id, key in ids.items()
    ])

    deadline = time.monotonic() + timeout_seconds
    while True:
        status = client.messages.batches.retrieve(batch.id)
        if status.processing_status == "ended":
            break
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"batch {batch.id} still {status.processing_status} after {timeout_seconds}s"
            )
        time.sleep(poll_seconds)

    out: dict[str, list[ExtractedClaim]] = {key: [] for key in chunk_texts}
    for result in client.messages.batches.results(batch.id):
        key = ids.get(result.custom_id)
        if key is not None and result.result.type == "succeeded":
            out[key] = _claims_from_message(result.result.message)
    return out


def _run_jobs(
    jobs: list[tuple[str, str]], system_prompt: str, client, model: str,
    max_workers: int, batch: bool,
) -> dict[str, list[ExtractedClaim]]:
    """Run `(job_key, chunk_text)` jobs and return `{job_key: claims}`.

    The synchronous path warms the cache ON PURPOSE: the first call runs ALONE so it writes the
    shared prefix, and only then do the rest fan out and read it. Firing all of them at once
    would have every in-flight call miss simultaneously and pay the 1.25x write premium — caching
    would then cost MORE than not caching at all. One serialized call turns N writes into
    1 write + (N-1) reads, at the price of one call's latency.
    """
    if not jobs:
        return {}
    if batch:
        return _call_model_batch(dict(jobs), system_prompt, client, model)

    first_key, first_text = jobs[0]
    out: dict[str, list[ExtractedClaim]] = {
        first_key: _call_model(first_text, system_prompt, client, model)
    }
    rest = jobs[1:]
    if rest:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(rest))) as pool:
            futures = [
                (key, pool.submit(_call_model, text, system_prompt, client, model))
                for key, text in rest
            ]
            for key, future in futures:
                out[key] = future.result()
    return out


# --- Coverage sweep (ADR-002 D-5) ------------------------------------------------------------
#
# Extraction is a model call and does not repeat itself: A-05 measured 65-77 verdicts on identical
# input across five live runs, and the eval gate failed 2 in 5 because one `stale_material` claim
# intermittently vanished — always the i-bond composite-rate sentence that sits beside the 2022
# historical trap. A dropped live-rate sentence means a genuinely stale video goes uncorrected.
#
# The obvious fix — pin the sampling — is NOT AVAILABLE. Verified against the live API on
# 2026-09-07: `temperature`, `top_p` and `top_k` all return 400 "deprecated for this model", and
# `seed`/`random_seed` are rejected as extra inputs. ADR-000 §4 previously listed `temperature` as
# supported; that was wrong and is corrected there.
#
# So determinism is not requested from the model — it is enforced in code, which is the same move
# the rest of Almanac makes. A conservative sweep marks every sentence that certainly carries a
# number (digits, or spelled-out numerals such as "three point one one percent"). Any marked
# sentence the first pass produced no claim for is sent back for one more targeted look. The model
# still reads; the code decides what counts as complete.

_ONES = r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
_TENS = r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_SCALE = r"hundred|thousand|million|billion"
_UNIT = r"percent|dollars?|point|basis points?"
_NUMWORD = rf"(?:{_ONES}|{_TENS}|{_SCALE})"

_HAS_DIGIT = re.compile(r"\d")
_NUMWORD_RUN = re.compile(rf"\b{_NUMWORD}\b[\s,-]+\b(?:{_NUMWORD}|{_UNIT})\b", re.I)
_NUMWORD_UNIT = re.compile(rf"\b{_NUMWORD}\b(?:\W+\w+){{0,2}}\W+\b(?:{_UNIT})\b", re.I)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")

# One targeted re-read only. A sentence the model declines twice is a finding, not a retry loop.
COVERAGE_PASSES = 1


def carries_a_number(sentence: str) -> bool:
    """True when a sentence certainly states a number, in digits or in words.

    Deliberately conservative: it should never claim a sentence carries a number when it does not,
    because every hit costs a second model call. Missing a borderline sentence only forfeits the
    safety net for it; the first pass still had its chance.
    """
    return bool(
        _HAS_DIGIT.search(sentence)
        or _NUMWORD_RUN.search(sentence)
        or _NUMWORD_UNIT.search(sentence)
    )


def number_bearing_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every sentence in `text` that carries a number."""
    return [
        (m.start(), m.end())
        for m in _SENTENCE.finditer(text)
        if m.group(0).strip() and carries_a_number(m.group(0))
    ]


def uncovered_sentences(text: str, claims: Iterable[Claim]) -> list[str]:
    """Number-bearing sentences that no claim overlaps — what the first pass failed to read."""
    covered: list[tuple[int, int]] = []
    for claim in claims:
        span = locate(text, claim.quote)
        if span is not None:
            covered.append(span)
    missing = []
    for start, end in number_bearing_spans(text):
        if not any(cs < end and start < ce for cs, ce in covered):
            missing.append(text[start:end].strip())
    return missing


@dataclass
class ExtractionStats:
    """What the run did, so a caller can see drops rather than infer them from a short list."""

    returned: int = 0
    dropped_unlocatable: int = 0
    dropped_duplicate: int = 0
    demoted_entity_key: int = 0
    # Claims the first pass missed and the coverage sweep recovered. Non-zero here is the
    # measurement of how often extraction under-reads; it should be reported, not hidden.
    recovered_by_sweep: int = 0


def _assemble(
    source: Source,
    raw: Iterable[ExtractedClaim],
    allowed: set[str],
    stats: ExtractionStats,
) -> list[Claim]:
    """Turn model output into Claims, assigning provenance and enforcing the verbatim rule."""
    located: list[tuple[int, Claim]] = []
    seen: set[tuple[int, int]] = set()

    for item in raw:
        stats.returned += 1
        span = locate(source.text, item.quote)
        if span is None:
            stats.dropped_unlocatable += 1
            continue
        start, end = _trim_to_limit(source.text, *span)
        if (start, end) in seen:
            stats.dropped_duplicate += 1
            continue
        seen.add((start, end))

        entity_key = _admit_entity_key(item.entity_key, allowed)
        if item.entity_key is not None and entity_key is None:
            stats.demoted_entity_key += 1

        located.append((
            start,
            Claim(
                source_id=source.source_id,
                locator=locator_for(source.locators, start),
                quote=source.text[start:end],
                claim_type=item.claim_type,
                entity_key=entity_key,
                value=item.value,
                unit=item.unit,
                year_hint=item.year_hint,
                confidence=max(0.0, min(1.0, item.confidence)),
            ),
        ))

    located.sort(key=lambda pair: pair[0])
    return [claim for _, claim in located]


def extract(text: str, source_id: str, locators: list) -> list[Claim]:
    """Label every numeric sentence in `text`, returning Claims with provenance attached.

    `locators` is a list of `Locator` spans over `text`; `Claim.locator` is looked up from the
    offset the quote was found at, never taken from the model.
    """
    source = Source(source_id, text, [Locator(*l) for l in locators])
    return extract_sources([source])[source_id]


def extract_sources(
    sources: list[Source], max_workers: int = MAX_CONCURRENCY, batch: bool = False
) -> dict[str, list[Claim]]:
    """Extract from several sources at once, sharing one bounded pool.

    Chunks from every source go into the same `ThreadPoolExecutor(max_workers=4)`, so a five-video
    run is four calls in flight rather than five sequential ones. The cap is on total in-flight
    requests, which is the number that matters to the API and to wall time.
    """
    from almanac.catalog import load_catalog

    allowed = set(load_catalog().keys())
    system_prompt = build_system_prompt()
    client, model = _client()

    # (job_key, source_id, chunk_text). A source may hold several chunks, so each job carries its
    # own key and the results are folded back per source — never matched by position.
    jobs: list[tuple[str, str, str]] = []
    for source in sources:
        for _, chunk_text in chunk(source):
            jobs.append((f"read{len(jobs)}", source.source_id, chunk_text))

    results: dict[str, list[ExtractedClaim]] = {s.source_id: [] for s in sources}
    produced = _run_jobs(
        [(key, text) for key, _, text in jobs], system_prompt, client, model, max_workers, batch
    )
    for key, source_id, _ in jobs:
        results[source_id].extend(produced.get(key, []))

    stats = ExtractionStats()
    by_source = {s.source_id: s for s in sources}
    assembled = {
        source_id: _assemble(by_source[source_id], raw, allowed, stats)
        for source_id, raw in results.items()
    }

    # Coverage sweep: re-read only the number-bearing sentences the first pass produced no claim
    # for. `_assemble` dedupes by span, so re-offering a sentence already covered is harmless.
    for _ in range(COVERAGE_PASSES):
        retry = {
            source_id: uncovered_sentences(by_source[source_id].text, claims)
            for source_id, claims in assembled.items()
        }
        retry = {sid: miss for sid, miss in retry.items() if miss}
        if not retry:
            break
        sweep_jobs = [
            (f"sweep{index}", source_id, "\n".join(miss))
            for index, (source_id, miss) in enumerate(retry.items())
        ]
        produced = _run_jobs(
            [(key, text) for key, _, text in sweep_jobs],
            system_prompt, client, model, max_workers, batch,
        )
        for key, source_id, _ in sweep_jobs:
            recovered = produced.get(key, [])
            stats.recovered_by_sweep += len(recovered)
            results[source_id].extend(recovered)
        assembled = {
            source_id: _assemble(by_source[source_id], raw, allowed, stats)
            for source_id, raw in results.items()
        }

    return assembled
