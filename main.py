from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import tempfile
from pathlib import Path
import webvtt
import whisper

app = FastAPI()

WHISPER_MODEL = "base"
LANGUAGE = "en"
ENABLE_WHISPER_FALLBACK = True  # set False if you want captions-only


class YouTubeRequest(BaseModel):
    youtube_url: str


def run_cmd(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    return result.stdout, result.stderr, result.returncode


def parse_vtt(vtt_path):
    transcript = []
    for caption in webvtt.read(vtt_path):
        transcript.append({
            "text": caption.text.replace("\n", " ").strip(),
            "start": caption.start_in_seconds,
            "end": caption.end_in_seconds
        })
    return transcript


def extract_transcript(youtube_url: str):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # 1. List subtitles
        stdout, _, _ = run_cmd(["yt-dlp", "--list-subs", youtube_url])

        has_manual = "Available subtitles" in stdout
        has_auto = "Available automatic captions" in stdout

        # 2. Manual captions
        if has_manual:
            run_cmd([
                "yt-dlp", "--skip-download",
                "--write-subs",
                "--sub-lang", LANGUAGE,
                "--sub-format", "vtt",
                "-o", str(tmpdir / "%(id)s"),
                youtube_url
            ])

            vtts = list(tmpdir.glob("*.vtt"))
            if vtts:
                return "youtube_manual_caption", parse_vtt(vtts[0])

        # 3. Auto captions
        if has_auto:
            run_cmd([
                "yt-dlp", "--skip-download",
                "--write-auto-subs",
                "--sub-lang", LANGUAGE,
                "--sub-format", "vtt",
                "-o", str(tmpdir / "%(id)s"),
                youtube_url
            ])

            vtts = list(tmpdir.glob("*.vtt"))
            if vtts:
                return "youtube_auto_caption", parse_vtt(vtts[0])

        # 4. Whisper fallback (optional)
        if not ENABLE_WHISPER_FALLBACK:
            raise HTTPException(
                status_code=400,
                detail="Captions not accessible and Whisper fallback disabled"
            )

        audio_path = tmpdir / "audio.m4a"
        run_cmd([
            "yt-dlp", "-f", "bestaudio",
            "-o", str(audio_path),
            youtube_url
        ])

        model = whisper.load_model(WHISPER_MODEL)
        result = model.transcribe(str(audio_path), language=LANGUAGE)

        transcript = [
            {
                "text": seg["text"].strip(),
                "start": seg["start"],
                "end": seg["end"]
            }
            for seg in result["segments"]
        ]

        return "whisper_fallback", transcript


@app.post("/extract-transcript")
def extract(req: YouTubeRequest):
    source, transcript = extract_transcript(req.youtube_url)
    return {
        "source": source,
        "transcript": transcript
    }
