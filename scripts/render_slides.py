#!/usr/bin/env python3
"""A-06 step 1 — render each corpus video to a 20-60s slideshow for manual upload.

WHY THIS IS BUILT THE WAY IT IS
-------------------------------
The obvious implementation is ffmpeg's `drawtext` filter. It is not available here:
this machine's ffmpeg 8.1.1 is compiled **without libfreetype and without libass**, so
neither `drawtext` nor `subtitles` exists. Nor is ImageMagick, rsvg-convert, PIL or
cairosvg installed, and no new dependency may be added without Zaeem's approval.

So the text is drawn by the only typesetter that is guaranteed present: PDF itself.
Each card is emitted as a hand-written one-page PDF using the **base-14 Helvetica**
fonts — those are built into every PDF renderer, so nothing is embedded and nothing is
installed — then rasterised to PNG by macOS `sips`. ffmpeg is asked to do only the one
job it can still do: sequence still images into H.264.

    card text -> PDF (base-14 Helvetica) -> sips -> PNG 1280x720 -> ffmpeg -> MP4

CONTENT RULES (issue A-06): no real brand assets and no real person's likeness. These
are plain typographic cards on a flat background, generated from the corpus text only.

The bullets are chosen by **code, not by a model**. `render_slides.py` is a build script,
but Almanac's standing rule is that only extract.py and notes.py may call a model, and
there is no reason to spend a model call on picking three sentences. Selection is
deterministic: sentences containing a numeral, sampled across the runtime so the cards
span the video rather than clustering in the intro.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from almanac.cli import load_env  # noqa: E402

CORPUS = ROOT / "corpus" / "channel"
OUT_DIR = ROOT / "renders"

WIDTH, HEIGHT = 1280, 720
MARGIN = 96

# DURATION: each video runs exactly as long as its own caption track.
#
# This deliberately overrides HAC-42's "20-60s slideshow" guidance, and the override is the
# point rather than drift. The corpus caption files run 159-168s (53-56 cues at a 3s cadence).
# A 30s video carrying a 162s caption track hides ~80% of its own subtitles and cuts off
# mid-sentence — a judge clicking through from the review page sees something visibly broken,
# which is the opposite of what the corpus was written to demonstrate. The 20-60s bound
# existed to keep rendering cheap, not because 30s is correct.
#
# Zaeem's call, recorded in docs/proofs/A-06.md and the A-06 walkthrough.
MIN_CARD_SECONDS = 6.0          # no card flashes past unread
DURATION_TOLERANCE = 2.0        # video vs caption-track end, asserted after encoding

# Narration is synthesized with the macOS built-in `say`. Text-to-speech, not a recording:
# no human time, no new dependency, nothing installed. Silence would be worse than useless —
# the previous build shipped an anullsrc track that measured -91.0 dB, i.e. digital silence
# that merely EXISTED. Anything quieter than this is treated as a failed mux, not as audio.
NARRATION_VOICE = "Samantha"

# --- Google Cloud Text-to-Speech (optional, opt-in via --engine google) ------------------
# Verified against the REST reference on 2026-09-07:
#   POST https://texttospeech.googleapis.com/v1/text:synthesize
#   body {input:{text}, voice:{languageCode,name}, audioConfig:{audioEncoding,speakingRate,...}}
#   response {"audioContent": "<base64>"}  — for LINEAR16 the bytes INCLUDE the WAV header,
#   so the decoded payload is a playable .wav with no conversion step.
# speakingRate is 0.25-4.0; sampleRateHertz 8000-48000.
#
# No new package: this is a plain `requests` POST, and `requests` is already a dependency.
# The key is read from the environment and never printed.
GOOGLE_TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
GOOGLE_TTS_VOICES_ENDPOINT = "https://texttospeech.googleapis.com/v1/voices"
GOOGLE_TTS_KEY_VAR = "GOOGLE_TTS_API_KEY"
# Studio voices are Google's long-form narration voices and are the most natural of the set.
GOOGLE_TTS_VOICE = "en-US-Studio-O"
GOOGLE_TTS_LANGUAGE = "en-US"
GOOGLE_TTS_SAMPLE_RATE = 44100
SILENCE_FLOOR_DB = -50.0
# atempo only ever speeds narration up to fit its cue slot, and only within ffmpeg's range.
ATEMPO_MIN, ATEMPO_MAX = 1.0, 2.0

# Flat palette. Deliberately generic — no channel branding exists and none is invented.
INK = (0.965, 0.969, 0.976)
DIM = (0.549, 0.635, 0.749)
ACCENT = (0.996, 0.780, 0.345)
BG = (0.055, 0.078, 0.118)
RULE = (0.153, 0.196, 0.271)

# --------------------------------------------------------------------------------------
# Base-14 Helvetica advance widths, units of 1/1000 em. Needed for line breaking: without
# real metrics the wrap is a guess and long lines run off the card.
# --------------------------------------------------------------------------------------
_HELV = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 "  # sp ! " # $ % & ' ( ) * + , - . /
    "556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 "  # 0-9 : ; < = > ?
    "1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 "  # @ A-O
    "667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 "   # P-Z [ \ ] ^ _
    "333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 "   # ` a-o
    "556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"        # p-z { | } ~
)
_HELV_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 "
    "556 556 556 556 556 556 556 556 556 556 333 333 584 584 584 611 "
    "975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 "
    "667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556 "
    "333 556 611 556 611 556 333 611 611 278 278 556 278 889 611 611 "
    "611 611 389 556 333 611 556 778 556 556 500 389 280 389 584"
)


def _widths(table: str) -> dict[str, int]:
    values = [int(v) for v in table.split()]
    assert len(values) == 95, f"width table has {len(values)} entries, expected 95"
    return {chr(32 + i): w for i, w in enumerate(values)}


REGULAR_W = _widths(_HELV)
BOLD_W = _widths(_HELV_BOLD)

# PDF's base-14 fonts are WinAnsi; the corpus is plain prose but may carry typographic
# punctuation. Fold it to ASCII rather than emit bytes the renderer will mangle.
_ASCII_FOLD = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
}


def to_ascii(text: str) -> str:
    for bad, good in _ASCII_FOLD.items():
        text = text.replace(bad, good)
    return "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)


def text_width(text: str, size: float, bold: bool) -> float:
    table = BOLD_W if bold else REGULAR_W
    return sum(table.get(ch, 556) for ch in text) * size / 1000.0


def wrap(text: str, size: float, bold: bool, max_width: float) -> list[str]:
    """Greedy wrap using real Helvetica metrics."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and text_width(candidate, size, bold) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------------------
# Minimal PDF writer. One page, no compression, no embedded fonts.
# --------------------------------------------------------------------------------------

def _escape(text: str) -> bytes:
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return out.encode("latin-1", "replace")


class Card:
    """Accumulates PDF content-stream operators for a single 1280x720 page."""

    def __init__(self) -> None:
        r, g, b = BG
        self.ops = [f"q {r:.3f} {g:.3f} {b:.3f} rg 0 0 {WIDTH} {HEIGHT} re f Q"]

    def rect(self, x: float, y: float, w: float, h: float, color) -> None:
        r, g, b = color
        self.ops.append(f"q {r:.3f} {g:.3f} {b:.3f} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f Q")

    def text(self, x: float, y: float, body: str, size: float, color, bold: bool) -> None:
        r, g, b = color
        font = "/F1" if bold else "/F2"
        self.ops.append(
            f"BT {font} {size:.1f} Tf {r:.3f} {g:.3f} {b:.3f} rg {x:.1f} {y:.1f} Td "
            f"({_escape(body).decode('latin-1')}) Tj ET"
        )

    def to_pdf(self) -> bytes:
        content = ("\n".join(self.ops) + "\n").encode("latin-1")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
                b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>"
                % (WIDTH, HEIGHT)
            ),
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % index + obj + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for offset in offsets:
            out += b"%010d 00000 n \n" % offset
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1, xref
        )
        return bytes(out)


# --------------------------------------------------------------------------------------
# Content selection — deterministic, no model call
# --------------------------------------------------------------------------------------

NUMERAL = re.compile(r"\d")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def caption_cues(srt_path: Path) -> list:
    """Parse the caption file with the `srt` library — the same parser A-02 used."""
    import srt as srt_lib

    return list(srt_lib.parse(srt_path.read_text(encoding="utf-8")))


def cue_slots(cues: list) -> list[tuple[float, float]]:
    """Slot each cue occupies on the timeline: its own start to the NEXT cue's start.

    Using the next start rather than the cue's own end means the slots tile the timeline with
    no gaps, so concatenating one audio segment per slot reproduces the caption timing exactly
    instead of accumulating drift across 55 cues.
    """
    slots = []
    for i, cue in enumerate(cues):
        start = cue.start.total_seconds()
        end = cues[i + 1].start.total_seconds() if i + 1 < len(cues) else cue.end.total_seconds()
        slots.append((start, max(end - start, 0.1)))
    return slots


def sentences_with_times(cues: list) -> list[tuple[str, float]]:
    """Rebuild sentences from cue text, each tagged with the time it starts being spoken.

    The timing is what lets a bullet card appear exactly when the narration reaches it, rather
    than on an arbitrary fixed schedule.
    """
    sentences: list[tuple[str, float]] = []
    buffer, start = "", 0.0
    for cue in cues:
        content = " ".join(cue.content.split())
        if not content:
            continue
        if not buffer:
            start = cue.start.total_seconds()
        buffer = f"{buffer} {content}".strip()
        while True:
            match = re.search(r"[.!?](\s|$)", buffer)
            if not match:
                break
            sentences.append((buffer[: match.end()].strip(), start))
            buffer = buffer[match.end():].strip()
            start = cue.end.total_seconds()
    if buffer:
        sentences.append((buffer, start))
    return sentences


def pick_bullets(sentences: list[tuple[str, float]], count: int = 3,
                 max_chars: int = 150) -> list[tuple[str, float]]:
    """Pick `count` numeric sentences spread across the runtime, keeping their start times.

    Almanac exists to check numbers, so a card carrying no number is a wasted card. Sampling
    at even positions stops all three bullets coming from the intro.
    """
    numeric = [item for item in sentences if NUMERAL.search(item[0])]
    pool = numeric if len(numeric) >= count else sentences
    if not pool:
        return []
    if len(pool) <= count:
        chosen = pool
    else:
        step = len(pool) / count
        chosen = [pool[min(int(i * step), len(pool) - 1)] for i in range(count)]

    bullets = []
    for text, start in chosen:
        clean = to_ascii(text).strip()
        if len(clean) > max_chars:
            clean = clean[:max_chars].rsplit(" ", 1)[0] + "..."
        bullets.append((clean, start))
    return bullets


def title_card(title: str, published: str) -> Card:
    card = Card()
    card.rect(MARGIN, HEIGHT - 150, 84, 6, ACCENT)
    card.text(MARGIN, HEIGHT - 210, "ALMANAC", 26, DIM, bold=True)

    lines = wrap(to_ascii(title), 54, True, WIDTH - 2 * MARGIN)
    y = 400 if len(lines) > 2 else 360
    for line in lines[:3]:
        card.text(MARGIN, y, line, 54, INK, bold=True)
        y -= 70

    card.rect(MARGIN, 190, WIDTH - 2 * MARGIN, 2, RULE)
    card.text(MARGIN, 140, f"Published {published[:10]}", 24, DIM, bold=False)
    card.text(MARGIN, 100, "Synthetic demo video - no financial advice", 24, DIM, bold=False)
    return card


def bullet_card(index: int, total: int, body: str) -> Card:
    card = Card()
    card.rect(MARGIN, HEIGHT - 150, 84, 6, ACCENT)
    card.text(MARGIN, HEIGHT - 210, f"{index} / {total}", 26, DIM, bold=True)

    lines = wrap(body, 42, False, WIDTH - 2 * MARGIN)
    y = 420
    for line in lines[:5]:
        card.text(MARGIN, y, line, 42, INK, bold=False)
        y -= 58

    card.rect(MARGIN, 140, WIDTH - 2 * MARGIN, 2, RULE)
    card.text(MARGIN, 96, "almanac - synthetic demo corpus", 22, DIM, bold=False)
    return card


def card_schedule(bullet_times: list[float], total: float, card_count: int) -> list[float]:
    """Card durations, so each bullet appears when the narration reaches it.

    Boundaries are pushed forward monotonically to keep every card on screen at least
    MIN_CARD_SECONDS — the first numeric sentence is often the opening line, which would
    otherwise give the title card zero duration. If honouring that would run past the end of
    the audio, the schedule falls back to even division rather than emitting a negative
    duration.
    """
    bounds = [0.0]
    for time in bullet_times:
        bounds.append(max(time, bounds[-1] + MIN_CARD_SECONDS))
    if bounds[-1] + MIN_CARD_SECONDS > total:
        step = total / card_count
        bounds = [i * step for i in range(card_count)]
    bounds.append(total)
    return [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]


# --------------------------------------------------------------------------------------
# Narration — macOS `say`, one segment per cue, placed on the caption timeline
# --------------------------------------------------------------------------------------

def _voice_available(voice: str) -> bool:
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    return any(line.split()[:1] == [voice] for line in result.stdout.splitlines())


def google_api_key() -> str:
    key = os.environ.get(GOOGLE_TTS_KEY_VAR, "").strip()
    if not key:
        raise RuntimeError(
            f"{GOOGLE_TTS_KEY_VAR} is not set. Add it to .env, then re-run. "
            "Enable the Cloud Text-to-Speech API and create a key in the same Google Cloud "
            "project as the OAuth client (almanac-hackathon-507916)."
        )
    return key


def list_google_voices(language: str = GOOGLE_TTS_LANGUAGE) -> list[dict]:
    """Ask the API which voices exist, rather than trusting a name from documentation."""
    import requests

    response = requests.get(
        GOOGLE_TTS_VOICES_ENDPOINT,
        params={"key": google_api_key(), "languageCode": language},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"voices.list HTTP {response.status_code}: {response.text[:300]}")
    return response.json().get("voices", [])


def google_tts(text: str, out_path: Path, voice: str) -> None:
    """Synthesize one cue to a WAV via the REST endpoint.

    LINEAR16 is requested because its base64 payload already carries a WAV header, so the
    decoded bytes are a playable file — one less conversion, and one less place to lose audio.
    """
    import base64

    import requests

    response = requests.post(
        GOOGLE_TTS_ENDPOINT,
        params={"key": google_api_key()},
        json={
            "input": {"text": text},
            "voice": {"languageCode": GOOGLE_TTS_LANGUAGE, "name": voice},
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": GOOGLE_TTS_SAMPLE_RATE,
            },
        },
        timeout=60,
    )
    if response.status_code != 200:
        # The body carries Google's own reason (API not enabled, key restricted, billing off).
        # Surfacing it verbatim is the difference between a fix and a guess.
        raise RuntimeError(f"text:synthesize HTTP {response.status_code}: {response.text[:400]}")
    audio = response.json().get("audioContent")
    if not audio:
        raise RuntimeError("text:synthesize returned no audioContent")
    out_path.write_bytes(base64.b64decode(audio))


def synthesize_cue(text: str, out_path: Path, engine: str, voice: str | None) -> None:
    """One cue of speech, from whichever engine was selected."""
    if engine == "google":
        google_tts(text or " ", out_path, voice or GOOGLE_TTS_VOICE)
        return
    command = ["say"]
    if voice:
        command += ["-v", voice]
    command += ["-o", str(out_path), text or " "]
    _run(command)


def synthesize_narration(
    cues: list, work: Path, slug: str, engine: str = "say", voice: str | None = None
) -> tuple[Path, float]:
    """Speak each cue and lay it in that cue's own slot, so audio and subtitles stay locked.

    Synthesising the whole script in one `say` call would be simpler and wrong: its natural
    duration is whatever it is, and the caption file's timings are fixed, so the two would
    drift apart within a few sentences. Per-cue synthesis makes alignment structural — each
    cue's speech starts exactly where its subtitle starts.

    Where speech overruns its slot it is sped up with `atempo` (never slowed, and never past
    2x, which is ffmpeg's per-filter limit); where it is short the slot is padded with
    silence, which keeps the natural pace and reads as a pause between sentences.
    """
    if engine == "say" and voice is None:
        voice = NARRATION_VOICE if _voice_available(NARRATION_VOICE) else None
    suffix = ".wav" if engine == "google" else ".aiff"
    slots = cue_slots(cues)

    segments: list[Path] = []
    for i, cue in enumerate(cues):
        text = to_ascii(" ".join(cue.content.split()))
        segment = work / f"{slug}-cue{i:03d}{suffix}"
        synthesize_cue(text, segment, engine, voice)
        segments.append(segment)

    filters, labels = [], []
    for i, (segment, (_, slot)) in enumerate(zip(segments, slots)):
        spoken = probe_duration(segment)
        tempo = min(max(spoken / slot, ATEMPO_MIN), ATEMPO_MAX) if slot > 0 else ATEMPO_MIN
        filters.append(
            f"[{i}:a]atempo={tempo:.4f},aresample=44100,apad,"
            f"atrim=0:{slot:.4f},asetpts=N/SR/TB[a{i}]"
        )
        labels.append(f"[a{i}]")
    filters.append("".join(labels) + f"concat=n={len(segments)}:v=0:a=1[out]")

    narration = work / f"{slug}-narration.wav"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for segment in segments:
        command += ["-i", str(segment)]
    command += ["-filter_complex", ";".join(filters), "-map", "[out]",
                "-ar", "44100", "-ac", "2", str(narration)]
    _run(command)

    for segment in segments:
        segment.unlink(missing_ok=True)
    return narration, probe_duration(narration)


def mean_volume_db(path: Path) -> float:
    """Read mean_volume from ffmpeg's volumedetect. Digital silence reports -91.0 dB."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    if not match:
        raise RuntimeError(f"volumedetect produced no mean_volume for {path.name}")
    return float(match.group(1))


# --------------------------------------------------------------------------------------
# Rasterise + encode
# --------------------------------------------------------------------------------------

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {(result.stderr or result.stdout)[-400:]}")
    return result


def rasterise(card: Card, pdf_path: Path, png_path: Path) -> None:
    pdf_path.write_bytes(card.to_pdf())
    _run([
        "sips", "-s", "format", "png", "-s", "formatOptions", "best",
        "--resampleHeightWidthMax", str(WIDTH),
        str(pdf_path), "--out", str(png_path),
    ])
    if not png_path.is_file():
        raise RuntimeError(f"sips produced no output for {pdf_path.name}")


def probe_duration(path: Path) -> float:
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ])
    return float(result.stdout.strip())


def probe_resolution(path: Path) -> str:
    result = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path),
    ])
    return result.stdout.strip()


def encode(pngs: list[tuple[Path, float]], audio: Path, total: float, out_path: Path) -> None:
    """Sequence stills into H.264 over the synthesized narration.

    Two ffmpeg behaviours have to be handled together, and the duration assertion in
    `render_video` caught what happens when only one of them is:

    * the concat demuxer drops the final segment unless the last image is repeated, and
    * that repeated entry INHERITS the preceding `duration` rather than getting none, which
      silently appended a phantom 51s to the first build (212.8s of video against a 161.8s
      caption track).

    So the repeat stays, and the output length is pinned with `-t` instead. `-shortest` is
    deliberately not used: it would trim to whichever stream happened to come out shorter and
    hide exactly the drift this function exists to preserve.
    """
    listing = []
    for png, seconds in pngs:
        listing.append(f"file '{png.as_posix()}'")
        listing.append(f"duration {seconds:.4f}")
    listing.append(f"file '{pngs[-1][0].as_posix()}'")

    list_path = out_path.parent / f"{out_path.stem}.concat.txt"
    list_path.write_text("\n".join(listing) + "\n", encoding="utf-8")

    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-t", f"{total:.4f}",
        "-movflags", "+faststart",
        str(out_path),
    ])
    list_path.unlink(missing_ok=True)


def render_video(slug: str, meta: dict, srt_path: Path, out_dir: Path, work: Path,
                 engine: str = "say", voice: str | None = None) -> dict:
    cues = caption_cues(srt_path)
    if not cues:
        raise RuntimeError(f"{slug}: caption file parsed to zero cues")
    captions_end = cues[-1].end.total_seconds()

    bullets = pick_bullets(sentences_with_times(cues))
    if len(bullets) != 3:
        raise RuntimeError(f"{slug}: expected 3 bullets, selected {len(bullets)}")

    narration, narration_seconds = synthesize_narration(cues, work, slug, engine, voice)

    cards = [title_card(meta["title"], meta.get("published_at", ""))]
    cards += [bullet_card(i, len(bullets), text) for i, (text, _) in enumerate(bullets, start=1)]
    durations = card_schedule([start for _, start in bullets], narration_seconds, len(cards))

    stills: list[tuple[Path, float]] = []
    for i, (card, seconds) in enumerate(zip(cards, durations)):
        pdf_path = work / f"{slug}-{i:02d}.pdf"
        png_path = work / f"{slug}-{i:02d}.png"
        rasterise(card, pdf_path, png_path)
        stills.append((png_path, seconds))

    out_path = out_dir / f"{slug}.mp4"
    encode(stills, narration, narration_seconds, out_path)

    # ---- verification, measured rather than assumed -------------------------------------
    duration = probe_duration(out_path)
    resolution = probe_resolution(out_path)
    volume = mean_volume_db(out_path)
    drift = abs(duration - captions_end)

    if resolution != f"{WIDTH}x{HEIGHT}":
        raise RuntimeError(f"{slug}: resolution {resolution} != {WIDTH}x{HEIGHT}")
    if drift > DURATION_TOLERANCE:
        raise RuntimeError(
            f"{slug}: video is {duration:.1f}s but its captions end at {captions_end:.1f}s "
            f"({drift:.1f}s adrift, tolerance {DURATION_TOLERANCE}s) — subtitles would run "
            "past the end of the video"
        )
    if volume <= SILENCE_FLOOR_DB:
        raise RuntimeError(
            f"{slug}: audio measures {volume:.1f} dB, at or below the {SILENCE_FLOOR_DB} dB "
            "silence floor. A track that merely EXISTS is what shipped last time; this is a "
            "failed narration mux, not audio."
        )

    return {
        "slug": slug,
        "path": out_path,
        "duration": duration,
        "captions_end": captions_end,
        "drift": drift,
        "resolution": resolution,
        "volume_db": volume,
        "bullets": [text for text, _ in bullets],
        "size_mb": out_path.stat().st_size / 1e6,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render corpus videos to upload-ready slideshows.")
    parser.add_argument("--out", default=str(OUT_DIR), help="output directory")
    parser.add_argument("--only", help="render a single corpus slug")
    parser.add_argument("--engine", choices=("say", "google"), default="say",
                        help="narration engine: macOS `say` (default) or Google Cloud TTS")
    parser.add_argument("--voice", help="voice name; defaults per engine")
    parser.add_argument("--list-voices", action="store_true",
                        help="print the Google TTS voices the API actually offers, and exit")
    args = parser.parse_args(argv)
    load_env()

    for tool in ("sips", "ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"missing required tool: {tool}", file=sys.stderr)
            return 2

    if args.list_voices:
        for v in sorted(list_google_voices(), key=lambda x: x["name"]):
            print(f"  {v['name']:<26} {v.get('ssmlGender',''):<7} {v.get('naturalSampleRateHertz','')}")
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".cards"
    work.mkdir(exist_ok=True)

    slugs = sorted(d.name for d in CORPUS.iterdir() if d.is_dir())
    if args.only:
        slugs = [s for s in slugs if s == args.only]
        if not slugs:
            print(f"no corpus dir named {args.only}", file=sys.stderr)
            return 2

    results = []
    for slug in slugs:
        meta = json.loads((CORPUS / slug / "meta.json").read_text(encoding="utf-8"))
        results.append(render_video(
            slug, meta, CORPUS / slug / "captions.srt", out_dir, work, args.engine, args.voice
        ))

    header = f"{'FILE':<38} {'VIDEO':>8} {'CAPTIONS':>9} {'DRIFT':>7} {'AUDIO':>9} {'RES':>10} {'MB':>6}"
    print(header)
    print("-" * len(header))
    for row in results:
        print(
            f"{row['path'].name:<38} {row['duration']:>7.1f}s {row['captions_end']:>8.1f}s "
            f"{row['drift']:>6.2f}s {row['volume_db']:>8.1f}dB {row['resolution']:>10} "
            f"{row['size_mb']:>6.2f}"
        )
    print("-" * len(header))
    worst_drift = max(row["drift"] for row in results)
    quietest = max(row["volume_db"] for row in results)
    print(
        f"{len(results)} video(s) in {out_dir}/ · every one at {WIDTH}x{HEIGHT}, "
        f"worst caption drift {worst_drift:.2f}s (tolerance {DURATION_TOLERANCE}s), "
        f"loudest-quiet {quietest:.1f}dB (floor {SILENCE_FLOOR_DB}dB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
