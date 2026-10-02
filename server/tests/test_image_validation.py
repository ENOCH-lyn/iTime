import hashlib
import struct
import zlib
from dataclasses import FrozenInstanceError
from io import BytesIO

import pytest
from PIL import Image

from itime.ai.image_validation import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    ValidatedImage,
    validate_image,
)
from itime.errors import AppError


def image_bytes(image_format: str, *, size: tuple[int, int] = (7, 5)) -> bytes:
    image = Image.new("RGB", size, color=(22, 96, 160))
    output = BytesIO()
    image.save(output, format=image_format)
    image.close()
    return output.getvalue()


def png_header(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


@pytest.mark.parametrize(
    ("image_format", "filename", "content_type"),
    [
        ("PNG", "schedule.png", "image/png"),
        ("JPEG", "schedule.jpg", "image/jpeg"),
        ("WEBP", "schedule.webp", "image/webp"),
    ],
)
def test_validate_real_supported_images(
    image_format: str,
    filename: str,
    content_type: str,
) -> None:
    contents = image_bytes(image_format)

    result = validate_image(contents, filename, content_type)

    assert result == ValidatedImage(
        format=image_format,
        content_type=content_type,
        width=7,
        height=5,
        byte_size=len(contents),
        sha256=hashlib.sha256(contents).hexdigest(),
    )


def test_validated_image_is_frozen() -> None:
    result = validate_image(image_bytes("PNG"), "schedule.png", "image/png")

    with pytest.raises(FrozenInstanceError):
        result.width = 10


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("schedule.jpg", "image/png"),
        ("schedule.png", "image/jpeg"),
        ("schedule", "image/png"),
    ],
)
def test_rejects_extension_or_content_type_mismatch(
    filename: str,
    content_type: str,
) -> None:
    with pytest.raises(AppError) as error:
        validate_image(image_bytes("PNG"), filename, content_type)

    assert error.value.code == "image_type_mismatch"
    assert error.value.status == 400


def test_rejects_unsupported_real_format() -> None:
    with pytest.raises(AppError) as error:
        validate_image(image_bytes("GIF"), "schedule.gif", "image/gif")

    assert error.value.code == "unsupported_image_format"


def test_rejects_animated_supported_image() -> None:
    first = Image.new("RGB", (4, 3), color="red")
    second = Image.new("RGB", (4, 3), color="blue")
    output = BytesIO()
    first.save(
        output,
        format="PNG",
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
    )
    first.close()
    second.close()

    with pytest.raises(AppError) as error:
        validate_image(output.getvalue(), "schedule.png", "image/png")

    assert error.value.code == "animated_image_not_supported"


def test_rejects_over_pixel_limit_from_header_before_decode() -> None:
    contents = png_header(MAX_IMAGE_PIXELS + 1, 1)

    with pytest.raises(AppError) as error:
        validate_image(contents, "wide.png", "image/png")

    assert error.value.code == "image_too_many_pixels"


@pytest.mark.parametrize(
    "contents",
    [
        png_header(2, 2),
        image_bytes("JPEG", size=(32, 32))[:-20],
    ],
    ids=["header-only", "truncated"],
)
def test_rejects_incomplete_or_damaged_images(contents: bytes) -> None:
    filename = "damaged.png" if contents.startswith(b"\x89PNG") else "damaged.jpg"
    content_type = "image/png" if filename.endswith(".png") else "image/jpeg"

    with pytest.raises(AppError) as error:
        validate_image(contents, filename, content_type)

    assert error.value.code == "invalid_image"


def test_accepts_exact_byte_limit_and_rejects_one_byte_over() -> None:
    image = image_bytes("PNG")
    at_limit = image + bytes(MAX_IMAGE_BYTES - len(image))

    result = validate_image(at_limit, "schedule.png", "image/png")
    assert result.byte_size == MAX_IMAGE_BYTES

    with pytest.raises(AppError) as error:
        validate_image(at_limit + b"x", "schedule.png", "image/png")

    assert error.value.code == "image_too_large"
    assert error.value.status == 413


def test_accepts_exact_pixel_limit() -> None:
    contents = image_bytes("PNG", size=(5_000, 4_000))

    result = validate_image(contents, "schedule.png", "image/png")

    assert result.width * result.height == MAX_IMAGE_PIXELS


def test_invalid_error_does_not_echo_input() -> None:
    marker = "private-image-marker"
    filename = f"{marker}.png"

    with pytest.raises(AppError) as error:
        validate_image(marker.encode(), filename, f"image/{marker}")

    assert error.value.code == "invalid_image"
    assert marker not in error.value.message
    assert error.value.details == {}
