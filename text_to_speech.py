import argparse
import io
import logging
import re
import sys
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from supertonic import TTS

# ----------------------------------------------------------------------
# Default constants
DEFAULT_SCRIPT_DIR = Path("Scripts")
DEFAULT_OUTPUT_DIR = Path("Output")
DEFAULT_VOICE = "M1"
DEFAULT_LANG = "en"
DEFAULT_PAUSE_MS = 0          # milliseconds of silence between lines
DEFAULT_WORKERS = 1

VALID_VOICES = {f"M{i}" for i in range(1, 6)} | {f"F{i}" for i in range(1, 6)}
DENOTER_RE = re.compile(r"^\{(M[1-5]|F[1-5])\}\s*", re.IGNORECASE)

# ----------------------------------------------------------------------
# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


def parse_lines(raw_text: str, default_voice: str) -> List[Tuple[str, str]]:
    """Parse script text into a list of (voice, text) tuples."""
    segments = []
    current_voice = default_voice

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue

        match = DENOTER_RE.match(line)
        if match:
            current_voice = match.group(1).upper()
            if current_voice not in VALID_VOICES:
                logger.warning(f"Unknown voice '{current_voice}', keeping previous voice")
            text = line[match.end():].strip()
        else:
            text = line

        if text:
            segments.append((current_voice, text))

    return segments


def synthesize_to_bytes(
    tts: TTS,
    text: str,
    voice_style,
    lang: str,
    sample_rate: Optional[int] = None
) -> Tuple[bytes, float]:
    """Synthesize a text line and return WAV bytes and duration."""
    wav, duration = tts.synthesize(text, voice_style=voice_style, lang=lang)
    
    # Convert numpy scalars to Python float to avoid formatting issues
    duration = float(duration)

    # Attempt in‑memory conversion
    try:
        if isinstance(wav, np.ndarray):
            sr = sample_rate or getattr(tts, "sample_rate", 22050)
            wav_int16 = (wav * 32767).astype(np.int16) if wav.dtype == np.float32 else wav.astype(np.int16)
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(wav_int16.tobytes())
            return buffer.getvalue(), duration
        elif isinstance(wav, tuple) and len(wav) == 2:
            sr, data = wav
            if isinstance(data, np.ndarray):
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as wf:
                    wf.setnchannels(1 if data.ndim == 1 else data.shape[1])
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(data.astype(np.int16).tobytes())
                return buffer.getvalue(), duration
    except Exception as e:
        logger.debug(f"In-memory conversion failed: {e}, falling back to temp file")

    # Fallback to temporary file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tts.save_audio(wav, str(tmp_path))
        data = tmp_path.read_bytes()
        return data, duration
    finally:
        tmp_path.unlink()


def merge_wavs_with_pause(wav_buffers: List[bytes], pause_samples: int, sample_rate: int) -> bytes:
    """Merge multiple WAVs, inserting silence between them."""
    if not wav_buffers:
        return b""

    out_buffer = io.BytesIO()
    with wave.open(out_buffer, "wb") as out_wav:
        params_set = False
        silence_frames = None

        for i, buf in enumerate(wav_buffers):
            with wave.open(io.BytesIO(buf), "rb") as in_wav:
                if not params_set:
                    out_wav.setparams(in_wav.getparams())
                    params_set = True
                    if pause_samples > 0:
                        nchannels = in_wav.getnchannels()
                        sampwidth = in_wav.getsampwidth()
                        silence_frames = b"\x00" * (nchannels * sampwidth * pause_samples)

                out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))
                if silence_frames and i < len(wav_buffers) - 1:
                    out_wav.writeframes(silence_frames)

    return out_buffer.getvalue()


def process_script(
    tts: TTS,
    txt_path: Path,
    out_path: Path,
    default_voice: str,
    lang: str,
    pause_ms: int,
    sample_rate: int,
    dry_run: bool
) -> None:
    """Process a single script file: parse, synthesize, and merge audio."""
    logger.info(f"Processing '{txt_path.name}' -> '{out_path.name}'")

    if dry_run:
        logger.info(f"  [DRY RUN] Would generate {out_path.name}")
        return

    try:
        raw_text = txt_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read {txt_path.name}: {e}")
        return

    if not raw_text.strip():
        logger.warning(f"'{txt_path.name}' is empty, skipping")
        return

    segments = parse_lines(raw_text, default_voice)
    if not segments:
        logger.warning(f"No speakable lines in '{txt_path.name}', skipping")
        return

    voices_used = sorted(set(v for v, _ in segments))
    logger.info(f"  {len(segments)} line(s), voices: {voices_used}")

    wav_buffers = []
    total_duration = 0.0

    for line_num, (voice, text) in enumerate(segments, 1):
        try:
            style = get_voice_style(tts, voice)
            wav_bytes, duration = synthesize_to_bytes(tts, text, style, lang, sample_rate)
            wav_buffers.append(wav_bytes)
            total_duration += duration

            preview = text[:60] + ("..." if len(text) > 60 else "")
            logger.debug(f"    [{line_num}/{len(segments)}] {voice}: \"{preview}\" ({duration:.2f}s)")
        except Exception as e:
            logger.error(f"  Synthesis failed for line {line_num} ('{text[:40]}...'): {e}")
            continue

    if not wav_buffers:
        logger.error(f"No audio generated for {txt_path.name}, skipping")
        return

    pause_samples = int(pause_ms * sample_rate / 1000)
    merged_wav = merge_wavs_with_pause(wav_buffers, pause_samples, sample_rate)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(merged_wav)
        logger.info(f"  Saved {out_path.name} | Total duration: {total_duration:.2f}s")
    except Exception as e:
        logger.error(f"Failed to write {out_path.name}: {e}")


@lru_cache(maxsize=None)
def get_voice_style(tts: TTS, voice: str):
    """Cached wrapper around tts.get_voice_style."""
    return tts.get_voice_style(voice_name=voice)


def main():
    parser = argparse.ArgumentParser(description="Batch TTS synthesis from text scripts")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_SCRIPT_DIR,
                        help=f"Folder with .txt scripts (default: {DEFAULT_SCRIPT_DIR})")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"Output folder for WAV files (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--default-voice", default=DEFAULT_VOICE, choices=VALID_VOICES,
                        help=f"Default voice when no denoter present (default: {DEFAULT_VOICE})")
    parser.add_argument("--lang", default=DEFAULT_LANG,
                        help=f"Language code (default: {DEFAULT_LANG})")
    parser.add_argument("--pause-ms", type=int, default=DEFAULT_PAUSE_MS,
                        help=f"Milliseconds of silence between lines (default: {DEFAULT_PAUSE_MS})")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Number of parallel script processors (default: {DEFAULT_WORKERS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only show what would be processed, do not synthesize")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging")

    # New arguments for single file mode
    parser.add_argument("--file", type=Path, help="Process a single .txt file instead of a directory")
    parser.add_argument("--output-path", type=Path, help="Explicit output WAV path (only with --file)")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # -------------------- Single file mode --------------------
    if args.file:
        txt_path = args.file
        if not txt_path.exists():
            logger.error(f"File '{txt_path}' not found")
            sys.exit(1)
        if txt_path.suffix.lower() != ".txt":
            logger.error(f"Input file must have .txt extension: '{txt_path}'")
            sys.exit(1)

        output_dir = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        if args.output_path:
            out_path = args.output_path
            if out_path.suffix.lower() != ".wav":
                out_path = out_path.with_suffix(".wav")
        else:
            out_path = output_dir / f"{txt_path.stem}.wav"

        logger.info("Loading TTS model...")
        tts = TTS(auto_download=True)
        sample_rate = getattr(tts, "sample_rate", 22050)

        process_script(
            tts, txt_path, out_path,
            args.default_voice, args.lang, args.pause_ms, sample_rate,
            args.dry_run
        )
        logger.info(f"Done. Output: {out_path}")
        return

    # -------------------- Batch mode (directory) --------------------
    input_dir = args.input_dir
    if not input_dir.exists():
        logger.error(f"Input directory '{input_dir}' does not exist")
        sys.exit(1)

    txt_files = sorted(input_dir.glob("*.txt"))
    if not txt_files:
        logger.warning(f"No .txt files found in '{input_dir}'")
        sys.exit(0)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading TTS model...")
    tts = TTS(auto_download=True)
    sample_rate = getattr(tts, "sample_rate", 22050)

    if not args.dry_run:
        logger.info(f"Default voice: {args.default_voice} | Language: {args.lang} | Pause: {args.pause_ms}ms")
    else:
        logger.info("DRY RUN – no audio will be generated")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = []
        for txt_path in txt_files:
            out_path = output_dir / f"{txt_path.stem}.wav"
            future = executor.submit(
                process_script,
                tts, txt_path, out_path,
                args.default_voice, args.lang, args.pause_ms, sample_rate,
                args.dry_run
            )
            futures.append(future)

        for future in as_completed(futures):
            future.result()  # propagate exceptions

    logger.info(f"All done! Output saved to '{output_dir}/'")


if __name__ == "__main__":
    main()