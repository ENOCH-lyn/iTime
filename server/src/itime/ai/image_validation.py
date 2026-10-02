import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Literal

from PIL import Image

from itime.errors import AppError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000

ImageFormat = Literal["PNG", "JPEG", "WEBP"]

_FORMAT_METADATA: dict[str, tuple[str, frozenset[str]]] = {
    "PNG": ("image/png", frozenset({".png"})),
    "JPEG": ("image/jpeg", frozenset({".jpeg", ".jpg"})),
    "WEBP": ("image/webp", frozenset({".webp"})),
}


@dataclass(frozen=True)
class ValidatedImage:
    format: ImageFormat
    content_type: str
    width: int
    height: int
    byte_size: int
    sha256: str


def _image_error(code: str, message: str, *, status: int = 400) -> AppError:
    return AppError(code, message, status=status)


def _normalized_content_type(content_type: str) -> str:
    if not isinstance(content_type, str):
        return ""
    return content_type.partition(";")[0].strip().lower()


def validate_image(contents: bytes, filename: str, content_type: str) -> ValidatedImage:
    byte_size = len(contents)
    if byte_size > MAX_IMAGE_BYTES:
        raise _image_error(
            "image_too_large",
            "图片不能超过 5 MiB。",
            status=413,
        )
    if not contents:
        raise _image_error("invalid_image", "图片为空、已损坏或无法完整解码。")

    try:
        with Image.open(BytesIO(contents)) as image:
            image_format = image.format
            if image_format not in _FORMAT_METADATA:
                raise _image_error(
                    "unsupported_image_format",
                    "仅支持 PNG、JPEG 或 WebP 图片。",
                )

            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise _image_error(
                    "image_too_many_pixels",
                    "图片像素数不能超过 20,000,000。",
                )
            if getattr(image, "n_frames", 1) != 1:
                raise _image_error(
                    "animated_image_not_supported",
                    "不支持动画或多帧图片。",
                )

            expected_content_type, expected_extensions = _FORMAT_METADATA[image_format]
            extension = PurePath(filename).suffix.lower() if isinstance(filename, str) else ""
            if (
                _normalized_content_type(content_type) != expected_content_type
                or extension not in expected_extensions
            ):
                raise _image_error(
                    "image_type_mismatch",
                    "图片内容、Content-Type 与文件扩展名必须一致。",
                )
            image.verify()

        with Image.open(BytesIO(contents)) as decoded:
            if (
                decoded.format != image_format
                or decoded.size != (width, height)
                or getattr(decoded, "n_frames", 1) != 1
            ):
                raise _image_error("invalid_image", "图片已损坏或无法完整解码。")
            decoded.load()
    except AppError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _image_error(
            "image_too_many_pixels",
            "图片像素数不能超过 20,000,000。",
        ) from exc
    except Exception as exc:
        raise _image_error("invalid_image", "图片已损坏或无法完整解码。") from exc

    return ValidatedImage(
        format=image_format,
        content_type=expected_content_type,
        width=width,
        height=height,
        byte_size=byte_size,
        sha256=hashlib.sha256(contents).hexdigest(),
    )
