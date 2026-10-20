"""Local speech-to-text for short timetable voice clips.

Audio is uploaded by the browser, converted to 16 kHz mono WAV with ffmpeg, and
recognized with a local Vosk Chinese model.  No audio leaves the machine and no
second network provider is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

from itime.errors import AppError

MAX_AUDIO_BYTES = 10 * 1024 * 1024
DEFAULT_MODEL = (
    Path(__file__).resolve().parents[3]
    / "models"
    / "vosk-model-small-cn-0.22"
)

_model = None
_model_lock = threading.Lock()


class LocalVoiceUnavailable(Exception):
    pass


def model_path() -> Path:
    return Path(os.environ.get("ITIME_VOSK_MODEL_PATH", DEFAULT_MODEL))


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            path = model_path()
            if not path.is_dir():
                raise LocalVoiceUnavailable("本地语音模型未安装。")
            try:
                from vosk import Model, SetLogLevel
            except ImportError as exc:
                raise LocalVoiceUnavailable("本地语音识别依赖未安装。") from exc
            SetLogLevel(-1)
            _model = Model(str(path))
        return _model


def transcribe_audio(contents: bytes, suffix: str) -> str:
    if not contents:
        raise AppError("voice_empty", "没有收到语音内容。", status=400)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise LocalVoiceUnavailable("缺少 ffmpeg，无法解码语音。")
    model = _get_model()
    with tempfile.TemporaryDirectory(prefix="itime-voice-") as directory:
        source = Path(directory) / f"input{suffix}"
        wav_path = Path(directory) / "voice.wav"
        source.write_bytes(contents)
        convert = subprocess.run(  # noqa: S603
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                str(wav_path),
            ],
            capture_output=True,
            timeout=60,
        )
        if convert.returncode != 0 or not wav_path.exists():
            raise AppError(
                "voice_decode_failed",
                "无法解析这段语音，请重新录制。",
                status=422,
            )
        try:
            from vosk import KaldiRecognizer
        except ImportError as exc:
            raise LocalVoiceUnavailable("本地语音识别依赖未安装。") from exc
        with wave.open(str(wav_path), "rb") as wav:
            recognizer = KaldiRecognizer(model, wav.getframerate())
            parts: list[str] = []
            while True:
                data = wav.readframes(4000)
                if not data:
                    break
                if recognizer.AcceptWaveform(data):
                    parts.append(json.loads(recognizer.Result()).get("text", ""))
            parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
        return " ".join(part for part in parts if part).strip()


async def transcribe_audio_async(contents: bytes, suffix: str) -> str:
    return await asyncio.to_thread(transcribe_audio, contents, suffix)