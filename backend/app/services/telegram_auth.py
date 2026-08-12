"""Validation of Telegram Mini App ``initData``.

Security model (this is the whole point of the module):

* The frontend sends the RAW, signed ``Telegram.WebApp.initData`` string — never
  ``initDataUnsafe`` — to our backend.
* We recompute Telegram's HMAC signature using the bot token (which lives ONLY
  on the backend) and compare it in constant time. Only if it matches do we
  trust the embedded user identity.
* We also reject payloads whose ``auth_date`` is too old (replay protection).

Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl


class TelegramAuthError(Exception):
    """Raised when initData cannot be trusted (bad signature, stale, malformed)."""


@dataclass
class TelegramUser:
    """The verified identity extracted from a valid initData payload."""

    telegram_user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None
    is_premium: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def _build_data_check_string(pairs: list[tuple[str, str]]) -> str:
    """Join all fields except ``hash`` as ``key=value`` lines, sorted by key."""
    return "\n".join(
        f"{key}={value}" for key, value in sorted(pairs) if key != "hash"
    )


def _secret_key(bot_token: str) -> bytes:
    """Telegram's WebApp secret key: HMAC-SHA256(key="WebAppData", msg=bot_token)."""
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int | None = 86400,
    now_ts: int | None = None,
) -> TelegramUser:
    """Validate a signed initData string and return the verified Telegram user.

    Raises :class:`TelegramAuthError` on any problem: empty input, missing hash,
    missing/blank bot token, signature mismatch, stale ``auth_date``, or a
    missing/invalid ``user`` object.

    ``now_ts`` is injectable so tests can pin "now" deterministically.
    """
    if not bot_token:
        # Guard rather than silently "validate" everything with an empty secret.
        raise TelegramAuthError("Telegram bot token is not configured")

    if not init_data or not init_data.strip():
        raise TelegramAuthError("initData is empty")

    # keep_blank_values so empty fields still participate in the check string.
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    if not pairs:
        raise TelegramAuthError("initData is malformed")

    data = dict(pairs)
    received_hash = data.get("hash")
    if not received_hash:
        raise TelegramAuthError("initData is missing its hash")

    data_check_string = _build_data_check_string(pairs)
    secret = _secret_key(bot_token)
    computed_hash = hmac.new(
        secret, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # Constant-time comparison to avoid timing side channels.
    if not hmac.compare_digest(computed_hash, received_hash):
        raise TelegramAuthError("initData signature is invalid")

    # Replay protection: reject stale payloads.
    if max_age_seconds is not None:
        auth_date_raw = data.get("auth_date")
        if not auth_date_raw:
            raise TelegramAuthError("initData is missing auth_date")
        try:
            auth_date = int(auth_date_raw)
        except (TypeError, ValueError) as exc:
            raise TelegramAuthError("initData has an invalid auth_date") from exc
        current = now_ts if now_ts is not None else _current_ts()
        if current - auth_date > max_age_seconds:
            raise TelegramAuthError("initData has expired")

    user = _extract_user(data.get("user"))
    return user


def _extract_user(user_raw: str | None) -> TelegramUser:
    if not user_raw:
        raise TelegramAuthError("initData is missing the user object")
    try:
        parsed = json.loads(user_raw)
    except (TypeError, ValueError) as exc:
        raise TelegramAuthError("initData user object is not valid JSON") from exc
    if not isinstance(parsed, dict) or "id" not in parsed:
        raise TelegramAuthError("initData user object has no id")
    try:
        user_id = int(parsed["id"])
    except (TypeError, ValueError) as exc:
        raise TelegramAuthError("initData user id is invalid") from exc

    return TelegramUser(
        telegram_user_id=user_id,
        username=parsed.get("username"),
        first_name=parsed.get("first_name"),
        last_name=parsed.get("last_name"),
        language_code=parsed.get("language_code"),
        is_premium=bool(parsed.get("is_premium", False)),
        raw=parsed,
    )


def _current_ts() -> int:
    # Isolated so tests can pass now_ts explicitly and avoid wall-clock flakiness.
    import time

    return int(time.time())
