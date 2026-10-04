import hashlib
import struct
from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal

from itime.errors import AppError

MAX_VOICE_BYTES = 2 * 1024 * 1024
MIN_VOICE_DURATION_MS = 250
MAX_VOICE_DURATION_MS = 60_000
VOICE_SAMPLE_RATE_HZ = 16_000
VOICE_CHANNEL_COUNT = 1
VOICE_SAMPLE_WIDTH_BITS = 16
WAV_HEADER_BYTES = 44


@dataclass(frozen=True)
class ValidatedVoiceAudio:
    content_type: Literal["audio/wav"]
    byte_size: int
    duration_ms: int
    sample_rate_hz: int
    channel_count: int
    sample_width_bits: int
    sha256: str


def _audio_error(code: str, message: str, *, status: int = 400) -> AppError:
    return AppError(code, message, status=status)


def _normalized_content_type(content_type: str) -> str:
    if not isinstance(content_type, str):
        return ""
    return content_type.partition(";")[0].strip().lower()


def _validate_type(filename: str, content_type: str) -> None:
    extension = (
        PurePath(filename).suffix.lower()
        if isinstance(filename, str)
        else ""
    )
    if extension != ".wav" or _normalized_content_type(content_type) != "audio/wav":
        raise _audio_error(
            "audio_type_mismatch",
            "录音内容、Content-Type 与文件扩展名必须一致。",
        )


def validate_voice_wav(
    contents: bytes,
    filename: str,
    content_type: str,
) -> ValidatedVoiceAudio:
    byte_size = len(contents)
    if byte_size > MAX_VOICE_BYTES:
        raise _audio_error(
            "audio_too_large",
            "录音不能超过 2 MiB。",
            status=413,
        )
    _validate_type(filename, content_type)
    if byte_size < WAV_HEADER_BYTES:
        raise _audio_error(
            "invalid_audio",
            "录音为空、已损坏或不是规范 PCM WAV。",
        )

    try:
        (
            riff,
            riff_size,
            wave,
            fmt_marker,
            fmt_size,
            audio_format,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            sample_width_bits,
            data_marker,
            data_size,
        ) = struct.unpack_from("<4sI4s4sIHHIIHH4sI", contents)
    except struct.error as exc:
        raise _audio_error(
            "invalid_audio",
            "录音为空、已损坏或不是规范 PCM WAV。",
        ) from exc

    if (
        riff != b"RIFF"
        or wave != b"WAVE"
        or fmt_marker != b"fmt "
        or fmt_size != 16
        or data_marker != b"data"
        or riff_size != byte_size - 8
        or data_size != byte_size - WAV_HEADER_BYTES
        or data_size <= 0
    ):
        raise _audio_error(
            "invalid_audio",
            "录音为空、已损坏或不是规范 PCM WAV。",
        )
    if audio_format != 1:
        raise _audio_error(
            "audio_encoding_unsupported",
            "录音必须使用未压缩 PCM 编码。",
        )
    if channels != VOICE_CHANNEL_COUNT:
        raise _audio_error(
            "audio_channels_unsupported",
            "录音必须使用单声道。",
        )
    if sample_rate != VOICE_SAMPLE_RATE_HZ:
        raise _audio_error(
            "audio_sample_rate_unsupported",
            "录音采样率必须为 16 kHz。",
        )
    if sample_width_bits != VOICE_SAMPLE_WIDTH_BITS:
        raise _audio_error(
            "audio_sample_width_unsupported",
            "录音必须使用 16 bit 采样。",
        )

    expected_block_align = channels * sample_width_bits // 8
    if (
        block_align != expected_block_align
        or byte_rate != sample_rate * expected_block_align
        or data_size % block_align
    ):
        raise _audio_error(
            "invalid_audio",
            "录音帧结构不完整，请重新录制。",
        )

    frame_count = data_size // block_align
    duration_ms = (frame_count * 1000 + sample_rate - 1) // sample_rate
    if duration_ms < MIN_VOICE_DURATION_MS:
        raise _audio_error(
            "audio_too_short",
            "录音至少需要 0.25 秒。",
        )
    if duration_ms > MAX_VOICE_DURATION_MS:
        raise _audio_error(
            "audio_too_long",
            "录音不能超过 60 秒。",
        )

    return ValidatedVoiceAudio(
        content_type="audio/wav",
        byte_size=byte_size,
        duration_ms=duration_ms,
        sample_rate_hz=sample_rate,
        channel_count=channels,
        sample_width_bits=sample_width_bits,
        sha256=hashlib.sha256(contents).hexdigest(),
    )
