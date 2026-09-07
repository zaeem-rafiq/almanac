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
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "corpus" / "channel"
OUT_DIR = ROOT / "renders"

WIDTH, HEIGHT = 1280, 720
MARGIN = 96

# Duration budget. 4 cards, 6s + 3x8s = 30s, comfortably inside the issue's 20-60s window.
TITLE_SECONDS = 6.0
BULLET_SECONDS = 8.0
MIN_TOTAL, MAX_TOTAL = 20.0, 60.0

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


def caption_sentences(srt_path: Path) -> list[str]:
    """Flatten an SRT into sentences, preserving order.

    Uses the `srt` library rather than a hand-rolled parser — it is already a project
    dependency and the corpus files are the same ones the API will hand back.
    """
    import srt as srt_lib

    subtitles = list(srt_lib.parse(srt_path.read_text(encoding="utf-8")))
    joined = " ".join(sub.content.replace("\n", " ").strip() for sub in subtitles)
    joined = re.sub(r"\s+", " ", joined)
    return [s.strip() for s in SENTENCE_SPLIT.split(joined) if s.strip()]


def pick_bullets(sentences: list[str], count: int = 3, max_chars: int = 150) -> list[str]:
    """Pick `count` numeric sentences spread across the runtime.

    Almanac exists to check numbers, so a card that carries no number is a wasted card.
    Sampling at even positions stops all three bullets coming from the intro.
    """
    numeric = [s for s in sentences if NUMERAL.search(s)]
    pool = numeric if len(numeric) >= count else sentences
    if not pool:
        return []
    if len(pool) <= count:
        chosen = pool
    else:
        step = len(pool) / count
        chosen = [pool[min(int(i * step), len(pool) - 1)] for i in range(count)]

    bullets = []
    for sentence in chosen:
        clean = to_ascii(sentence).strip()
        if len(clean) > max_chars:
            clean = clean[:max_chars].rsplit(" ", 1)[0] + "..."
        bullets.append(clean)
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


def encode(pngs: list[tuple[Path, float]], out_path: Path) -> None:
    """Sequence stills into H.264 with a silent stereo track.

    The concat demuxer needs the final image repeated without a duration, otherwise its
    last segment is dropped. A silent AAC track is included because a video with no audio
    stream at all is a common source of YouTube processing oddities.
    """
    total = sum(seconds for _, seconds in pngs)
    listing = []
    for png, seconds in pngs:
        listing.append(f"file '{png.as_posix()}'")
        listing.append(f"duration {seconds}")
    listing.append(f"file '{pngs[-1][0].as_posix()}'")

    list_path = out_path.parent / f"{out_path.stem}.concat.txt"
    list_path.write_text("\n".join(listing) + "\n", encoding="utf-8")

    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-f", "lavfi", "-t", f"{total:.2f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ])
    list_path.unlink(missing_ok=True)


def render_video(slug: str, meta: dict, srt_path: Path, out_dir: Path, work: Path) -> dict:
    bullets = pick_bullets(caption_sentences(srt_path))
    if len(bullets) != 3:
        raise RuntimeError(f"{slug}: expected 3 bullets, selected {len(bullets)}")

    cards: list[tuple[Card, float]] = [
        (title_card(meta["title"], meta.get("published_at", "")), TITLE_SECONDS)
    ]
    for i, bullet in enumerate(bullets, start=1):
        cards.append((bullet_card(i, len(bullets), bullet), BULLET_SECONDS))

    stills: list[tuple[Path, float]] = []
    for i, (card, seconds) in enumerate(cards):
        pdf_path = work / f"{slug}-{i:02d}.pdf"
        png_path = work / f"{slug}-{i:02d}.png"
        rasterise(card, pdf_path, png_path)
        stills.append((png_path, seconds))

    out_path = out_dir / f"{slug}.mp4"
    encode(stills, out_path)

    duration = probe_duration(out_path)
    resolution = probe_resolution(out_path)
    if not MIN_TOTAL <= duration <= MAX_TOTAL:
        raise RuntimeError(f"{slug}: {duration:.1f}s is outside the {MIN_TOTAL}-{MAX_TOTAL}s window")
    if resolution != f"{WIDTH}x{HEIGHT}":
        raise RuntimeError(f"{slug}: resolution {resolution} != {WIDTH}x{HEIGHT}")

    return {
        "slug": slug,
        "path": out_path,
        "duration": duration,
        "resolution": resolution,
        "bullets": bullets,
        "size_mb": out_path.stat().st_size / 1e6,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render corpus videos to upload-ready slideshows.")
    parser.add_argument("--out", default=str(OUT_DIR), help="output directory")
    parser.add_argument("--only", help="render a single corpus slug")
    args = parser.parse_args(argv)

    for tool in ("sips", "ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            print(f"missing required tool: {tool}", file=sys.stderr)
            return 2

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
        results.append(render_video(slug, meta, CORPUS / slug / "captions.srt", out_dir, work))

    print(f"{'FILE':<40} {'DUR':>7} {'RES':>10} {'MB':>6}")
    print("-" * 68)
    for row in results:
        print(
            f"{row['path'].name:<40} {row['duration']:>6.1f}s "
            f"{row['resolution']:>10} {row['size_mb']:>6.2f}"
        )
    print("-" * 68)
    print(f"{len(results)} video(s) in {out_dir}/ — every one inside {MIN_TOTAL:.0f}-{MAX_TOTAL:.0f}s at {WIDTH}x{HEIGHT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
