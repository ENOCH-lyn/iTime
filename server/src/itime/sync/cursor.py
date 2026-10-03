import base64
import binascii
import hmac
import json
from dataclasses import dataclass
from uuid import UUID

from itime.auth.security import Security
from itime.errors import AppError

CURSOR_VERSION = 1
MAX_CURSOR_LENGTH = 512


@dataclass(frozen=True)
class SyncCursor:
    owner_id: UUID
    sequence: int


def invalid_cursor() -> AppError:
    return AppError(
        "invalid_sync_cursor",
        "同步游标无效，请刷新当前数据后重试。",
        status=400,
    )


def encode_sync_cursor(security: Security, cursor: SyncCursor) -> str:
    payload = json.dumps(
        {
            "owner_id": str(cursor.owner_id),
            "sequence": cursor.sequence,
            "v": CURSOR_VERSION,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = security.keyed_digest(f"sync:{encoded}")
    return f"{encoded}.{signature}"


def decode_sync_cursor(
    security: Security,
    token: str,
    *,
    owner_id: UUID,
) -> SyncCursor:
    if not token or len(token) > MAX_CURSOR_LENGTH or token.count(".") != 1:
        raise invalid_cursor()
    encoded, signature = token.split(".", 1)
    expected = security.keyed_digest(f"sync:{encoded}")
    if len(signature) != len(expected) or not hmac.compare_digest(
        signature,
        expected,
    ):
        raise invalid_cursor()
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"owner_id", "sequence", "v"}
            or payload["v"] != CURSOR_VERSION
            or isinstance(payload["sequence"], bool)
            or not isinstance(payload["sequence"], int)
            or payload["sequence"] < 0
        ):
            raise ValueError("invalid payload")
        parsed_owner = UUID(payload["owner_id"])
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise invalid_cursor() from exc
    if parsed_owner != owner_id:
        raise invalid_cursor()
    return SyncCursor(owner_id=parsed_owner, sequence=payload["sequence"])
