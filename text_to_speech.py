from supertonic import TTS
from pathlib import Path
import re
import sys
import wave
import io
import tempfile

SCRIPTS_DIR = Path("Scripts") # Source txt files directory
OUTPUT_DIR = Path("Output")   # Output WAV files directory
DEFAULT_VOICE = "M1"          # Default voice if no denoter is specified in the script
LANG = "en"                   # Language code for synthesis (e.g., "en" for English)

VALID_VOICES = {f"M{i}" for i in range(1, 6)} | {f"F{i}" for i in range(1, 6)} # Valid voice denoters (M1-M5, F1-F5)
DENOTER_RE = re.compile(r"^\{(M[1-5]|F[1-5])\}\s*", re.IGNORECASE)             # Regex to match voice denoters at the start of a line (e.g., "{M1}", "{F3}")

# Parse script text into a list of (voice, text) tuples.
# Lines prefixed with {M1}, {F3}, etc. use that voice.
# Lines without a denoter inherit the last used voice (or DEFAULT_VOICE).
# Blank lines are skipped.
def parse_lines(raw_text: str) -> list[tuple[str, str]]:
    segments = []
    current_voice = DEFAULT_VOICE

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue

        match = DENOTER_RE.match(line)
        if match:
            current_voice = match.group(1).upper()
            text = line[match.end():].strip()
        else:
            text = line

        if text:
            segments.append((current_voice, text))

    return segments

# Merge a list of in-memory WAV byte strings into one WAV byte string."""
def merge_wavs(wav_buffers: list[bytes]) -> bytes:
    out_buffer = io.BytesIO()

    with wave.open(out_buffer, "wb") as out_wav:
        params_set = False
        for buf in wav_buffers:
            with wave.open(io.BytesIO(buf), "rb") as in_wav:
                if not params_set:
                    out_wav.setparams(in_wav.getparams())
                    params_set = True
                out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))

    return out_buffer.getvalue()

# Synthesize and return raw WAV bytes by writing to a temp file.
def synthesize_to_bytes(tts: TTS, text: str, style) -> tuple[bytes, float]:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    wav, duration = tts.synthesize(text, voice_style=style, lang=LANG)
    tts.save_audio(wav, str(tmp_path))
    data = tmp_path.read_bytes()
    tmp_path.unlink()
    return data, duration

def process_script(tts: TTS, voice_cache: dict, txt_path: Path, out_path: Path, index: int, total: int):
    raw_text = txt_path.read_text(encoding="utf-8").strip()

    if not raw_text:
        print(f"[{index}/{total}] Skipping '{txt_path.name}' (empty file)")
        return

    segments = parse_lines(raw_text)

    if not segments:
        print(f"[{index}/{total}] Skipping '{txt_path.name}' (no speakable lines)")
        return

    print(f"[{index}/{total}] Processing '{txt_path.name}' -> '{out_path.name}'")
    print(f"         {len(segments)} line(s) across voices: {sorted({v for v, _ in segments})}")

    wav_buffers = []
    total_duration = 0.0

    for line_num, (voice, text) in enumerate(segments, 1):
        # Cache voice styles so we don't re-fetch the same one repeatedly
        if voice not in voice_cache:
            voice_cache[voice] = tts.get_voice_style(voice_name=voice)
        style = voice_cache[voice]

        wav_bytes, duration = synthesize_to_bytes(tts, text, style)
        wav_buffers.append(wav_bytes)
        total_duration += duration
        preview = text[:60] + ("..." if len(text) > 60 else "")
        print(f"         [{line_num}/{len(segments)}] {voice}: \"{preview}\" ({duration}s)")

    merged = merge_wavs(wav_buffers)
    out_path.write_bytes(merged)
    print(f"         Merged -> {out_path.name} | Total: {total_duration}s\n")


def main():
    if not SCRIPTS_DIR.exists():
        print(f"Error: '{SCRIPTS_DIR}' folder not found.")
        sys.exit(1)

    txt_files = sorted(SCRIPTS_DIR.glob("*.txt"))

    if not txt_files:
        print(f"No .txt files found in '{SCRIPTS_DIR}'.")
        sys.exit(0)

    OUTPUT_DIR.mkdir(exist_ok=True)

    print("Loading TTS model...")
    tts = TTS(auto_download=True)
    voice_cache: dict = {}
    print(f"Default voice: {DEFAULT_VOICE} | Language: {LANG}\n")

    total = len(txt_files)
    for i, txt_path in enumerate(txt_files, 1):
        out_path = OUTPUT_DIR / f"{txt_path.stem}.wav"
        process_script(tts, voice_cache, txt_path, out_path, i, total)

    print(f"All done! WAV files saved to '{OUTPUT_DIR}/'")


if __name__ == "__main__":
    main()