import hashlib
import struct
from importlib import resources

import pytest

from itime.ai.audio_validation import (
    MAX_VOICE_BYTES,
    MAX_VOICE_DURATION_MS,
    MIN_VOICE_DURATION_MS,
    validate_voice_wav,
)
from itime.errors import AppError


def wav_bytes(
    *,
    duration_ms: int = 1000,
    channels: int = 1,
    sample_rate: int = 16_000,
    sample_width_bits: int = 16,
    audio_format: int = 1,
) -> bytes:
    frame_count = (sample_rate * duration_ms) // 1000
    block_align = channels * sample_width_bits // 8
    data = b"\0" * (frame_count * block_align)
    fmt = struct.pack(
        "<HHIIHH",
        audio_format,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        sample_width_bits,
    )
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def assert_audio_error(
    contents: bytes,
    code: str,
    *,
    filename: str = "voice.wav",
    content_type: str = "audio/wav",
) -> None:
    with pytest.raises(AppError) as raised:
        validate_voice_wav(contents, filename, content_type)
    assert raised.value.code == code


def test_validate_voice_wav_returns_authoritative_metadata():
    contents = wav_bytes(duration_ms=1250)
    result = validate_voice_wav(
        contents,
        "voice.wav",
        "audio/wav; charset=binary",
    )

    assert result.content_type == "audio/wav"
    assert result.byte_size == len(contents)
    assert result.duration_ms == 1250
    assert result.sample_rate_hz == 16_000
    assert result.channel_count == 1
    assert result.sample_width_bits == 16
    assert result.sha256 == hashlib.sha256(contents).hexdigest()


def test_packaged_transcription_probe_uses_canonical_voice_format():
    contents = (
        resources.files("itime.ai")
        .joinpath("assets/transcription_probe.wav")
        .read_bytes()
    )
    result = validate_voice_wav(
        contents,
        "itime-voice-check.wav",
        "audio/wav",
    )
    assert result.byte_size == len(contents)
    assert MIN_VOICE_DURATION_MS <= result.duration_ms <= MAX_VOICE_DURATION_MS


@pytest.mark.parametrize("duration_ms", [MIN_VOICE_DURATION_MS, MAX_VOICE_DURATION_MS])
def test_validate_voice_wav_accepts_duration_boundaries(duration_ms):
    result = validate_voice_wav(
        wav_bytes(duration_ms=duration_ms),
        "voice.wav",
        "audio/wav",
    )
    assert result.duration_ms == duration_ms


@pytest.mark.parametrize(
    ("duration_ms", "code"),
    [
        (MIN_VOICE_DURATION_MS - 1, "audio_too_short"),
        (MAX_VOICE_DURATION_MS + 1, "audio_too_long"),
    ],
)
def test_validate_voice_wav_rejects_duration_outside_bounds(duration_ms, code):
    assert_audio_error(wav_bytes(duration_ms=duration_ms), code)


@pytest.mark.parametrize(
    ("channels", "sample_rate", "sample_width_bits", "audio_format", "code"),
    [
        (2, 16_000, 16, 1, "audio_channels_unsupported"),
        (1, 48_000, 16, 1, "audio_sample_rate_unsupported"),
        (1, 16_000, 8, 1, "audio_sample_width_unsupported"),
        (1, 16_000, 16, 3, "audio_encoding_unsupported"),
    ],
)
def test_validate_voice_wav_rejects_noncanonical_pcm(
    channels,
    sample_rate,
    sample_width_bits,
    audio_format,
    code,
):
    assert_audio_error(
        wav_bytes(
            channels=channels,
            sample_rate=sample_rate,
            sample_width_bits=sample_width_bits,
            audio_format=audio_format,
        ),
        code,
    )


@pytest.mark.parametrize(
    ("contents", "code"),
    [
        (b"", "invalid_audio"),
        (b"not-a-wave-file", "invalid_audio"),
        (wav_bytes()[:-1], "invalid_audio"),
    ],
)
def test_validate_voice_wav_rejects_empty_corrupt_or_truncated_data(
    contents,
    code,
):
    assert_audio_error(contents, code)


def test_validate_voice_wav_rejects_extra_chunks_and_trailing_bytes():
    original = wav_bytes()
    extra = b"JUNK" + struct.pack("<I", 4) + b"test"
    with_extra_chunk = (
        original[:4]
        + struct.pack("<I", len(original) + len(extra) - 8)
        + original[8:]
        + extra
    )
    assert_audio_error(with_extra_chunk, "invalid_audio")
    assert_audio_error(original + b"trailing", "invalid_audio")


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("voice.webm", "audio/wav"),
        ("voice.wav", "audio/webm"),
        ("voice.WAV", "application/octet-stream"),
    ],
)
def test_validate_voice_wav_requires_matching_name_and_content_type(
    filename,
    content_type,
):
    assert_audio_error(
        wav_bytes(),
        "audio_type_mismatch",
        filename=filename,
        content_type=content_type,
    )


def test_validate_voice_wav_rejects_oversized_body_before_parsing():
    assert_audio_error(
        b"x" * (MAX_VOICE_BYTES + 1),
        "audio_too_large",
    )
