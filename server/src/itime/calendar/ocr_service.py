"""Local OCR fallback for timetable screenshots.

The configured chat provider may be text-only (for example DeepSeek chat), so
images are converted to text locally before the AI import request is built.
The OCR engine is loaded lazily to keep worker startup fast.
"""

from __future__ import annotations

from io import BytesIO
from threading import Lock

import numpy as np
from PIL import Image, UnidentifiedImageError

MIN_OCR_SCORE = 0.45
MAX_OCR_EDGE = 3200


class OcrUnavailable(Exception):
    pass


_engine = None
_engine_lock = Lock()
_call_lock = Lock()


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise OcrUnavailable("本地 OCR 引擎未安装。") from exc
            _engine = RapidOCR()
    return _engine


def extract_calendar_text(image: bytes) -> str:
    if not image:
        raise OcrUnavailable("图片内容为空。")
    try:
        picture = Image.open(BytesIO(image)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise OcrUnavailable("无法读取图片。") from exc
    if max(picture.size) > MAX_OCR_EDGE:
        picture.thumbnail((MAX_OCR_EDGE, MAX_OCR_EDGE))
    try:
        with _call_lock:
            result, _ = _get_engine()(np.array(picture))
    except Exception as exc:
        raise OcrUnavailable("OCR 识别失败。") from exc

    lines: list[tuple[float, float, str]] = []
    for item in result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box, text, score = item[0], item[1], item[2]
        if float(score) < MIN_OCR_SCORE:
            continue
        value = str(text).strip()
        if not value:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        lines.append((min(ys), min(xs), value))
    lines.sort(key=lambda item: (round(item[0] / 18), item[1]))
    content = "\n".join(value for _, _, value in lines)
    if len(content) < 10:
        raise OcrUnavailable("图片中没有识别到足够的文字。")
    return content
