"""경험정리 티켓 검증 테스트"""

import time

import jwt
import pytest

from features.experience_map.errors import (
    SessionForbiddenError,
    TicketExpiredError,
    TicketInvalidError,
)
from features.experience_map.ticket import (
    TicketPayload,
    extract_bearer_token,
    verify_ticket,
)

SECRET = "ticket-signing-secret-with-32plus-bytes"
OTHER_SECRET = "another-secret-with-32plus-bytes!!"
SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
OTHER_SESSION_ID = "11111111-2222-3333-4444-555555555555"


def make_ticket(
    *,
    sub: str = "123",
    sid: str = SESSION_ID,
    secret: str = SECRET,
    expires_in: int = 300,
    algorithm: str = "HS256",
    **overrides,
) -> str:
    now = int(time.time())
    payload = {"sub": sub, "sid": sid, "iat": now, "exp": now + expires_in}
    payload.update(overrides)
    return jwt.encode(payload, secret, algorithm=algorithm)


# ===== Bearer 헤더 =====


def test_extract_bearer_token():
    assert extract_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"


def test_extract_bearer_token_is_case_insensitive():
    assert extract_bearer_token("bearer abc") == "abc"


@pytest.mark.parametrize("header", [None, "", "abc.def", "Basic dXNlcjpwdw==", "Bearer "])
def test_extract_bearer_token_rejects_bad_headers(header):
    with pytest.raises(TicketInvalidError):
        extract_bearer_token(header)


# ===== 검증 3단계 =====


def test_verify_returns_payload():
    payload = verify_ticket(make_ticket(), SECRET, SESSION_ID)

    assert isinstance(payload, TicketPayload)
    assert payload.sub == "123"
    assert payload.sid == SESSION_ID


def test_verify_rejects_forged_signature():
    """다른 키로 서명한 티켓은 거부된다."""
    with pytest.raises(TicketInvalidError):
        verify_ticket(make_ticket(secret=OTHER_SECRET), SECRET, SESSION_ID)


def test_verify_rejects_tampered_payload():
    """payload를 바꾸면 서명이 깨진다."""
    token = make_ticket(sub="123")
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload[:-4]}AAAA.{signature}"

    with pytest.raises(TicketInvalidError):
        verify_ticket(tampered, SECRET, SESSION_ID)


def test_verify_rejects_expired_ticket():
    with pytest.raises(TicketExpiredError):
        verify_ticket(make_ticket(expires_in=-1), SECRET, SESSION_ID)


def test_verify_rejects_session_mismatch():
    """서명이 유효해도 다른 사람의 session_id 경로에는 쓸 수 없다."""
    with pytest.raises(SessionForbiddenError):
        verify_ticket(make_ticket(sid=OTHER_SESSION_ID), SECRET, SESSION_ID)


def test_signature_is_checked_before_expiry():
    """위조된 만료 티켓은 ticket_invalid 다. 만료 여부를 먼저 알려주지 않는다."""
    token = make_ticket(secret=OTHER_SECRET, expires_in=-1)

    with pytest.raises(TicketInvalidError):
        verify_ticket(token, SECRET, SESSION_ID)


def test_expiry_is_checked_before_session_match():
    """만료된 티켓은 sid가 달라도 ticket_expired 다."""
    token = make_ticket(sid=OTHER_SESSION_ID, expires_in=-1)

    with pytest.raises(TicketExpiredError):
        verify_ticket(token, SECRET, SESSION_ID)


# ===== 알고리즘 위조 =====


def test_verify_rejects_none_algorithm():
    """alg=none 토큰을 받아들이면 누구나 티켓을 만들 수 있다."""
    now = int(time.time())
    token = jwt.encode(
        {"sub": "123", "sid": SESSION_ID, "iat": now, "exp": now + 300},
        key="",
        algorithm="none",
    )

    with pytest.raises(TicketInvalidError):
        verify_ticket(token, SECRET, SESSION_ID)


def test_verify_rejects_other_hmac_algorithm():
    """HS256만 허용한다."""
    with pytest.raises(TicketInvalidError):
        verify_ticket(make_ticket(algorithm="HS512"), SECRET, SESSION_ID)


# ===== payload 형식 =====


@pytest.mark.parametrize("missing", ["sub", "sid", "exp"])
def test_verify_rejects_incomplete_payload(missing):
    now = int(time.time())
    claims = {"sub": "123", "sid": SESSION_ID, "iat": now, "exp": now + 300}
    claims.pop(missing)
    token = jwt.encode(claims, SECRET, algorithm="HS256")

    with pytest.raises((TicketInvalidError, TicketExpiredError)):
        verify_ticket(token, SECRET, SESSION_ID)


def test_verify_rejects_garbage_token():
    with pytest.raises(TicketInvalidError):
        verify_ticket("not-a-jwt", SECRET, SESSION_ID)
