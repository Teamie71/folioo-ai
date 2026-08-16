"""경험정리 티켓 검증

프론트가 AI 서버 SSE에 직결하므로 세션 단위 단기 자격증명이 필요하다.
메인 서버가 로그인 인증 뒤 HS256으로 서명하고, AI 서버는 검증만 한다.

검증은 **세 단계를 순서대로** 밟는다 (API 명세 2-1).

```text
① 서명       → ticket_invalid
② 만료       → ticket_expired
③ sid == path session_id → session_forbidden
```

세 번째가 세션 탈취를 막는다. 서명이 유효한 티켓이라도 다른 사람의 `session_id`
경로에는 쓸 수 없다.

서명 키는 `EXPMAP_TICKET_SECRET`이며 **`AI_SERVICE_API_KEY`를 재사용하지 않는다.**
두 키의 회전 주기가 다르고, API 키가 유출되면 티켓 위조까지 가능해진다.
"""

import logging

import jwt
from pydantic import BaseModel, Field

from features.experience_map.errors import (
    SessionForbiddenError,
    TicketExpiredError,
    TicketInvalidError,
)

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
BEARER_PREFIX = "bearer "


class TicketPayload(BaseModel):
    """티켓 payload (API 명세 2-1)"""

    sub: str = Field(..., description="사용자 ID (십진 문자열)")
    sid: str = Field(..., description="세션 UUID")
    iat: int = Field(..., description="발급 시각")
    exp: int = Field(..., description="만료 시각")


def extract_bearer_token(authorization: str | None) -> str:
    """`Authorization: Bearer {ticket}` 헤더에서 토큰을 꺼낸다.

    Raises:
        TicketInvalidError: 헤더가 없거나 Bearer 형식이 아닌 경우
    """
    if not authorization:
        raise TicketInvalidError("인증 헤더가 없습니다.")

    if not authorization.lower().startswith(BEARER_PREFIX):
        raise TicketInvalidError("Bearer 형식이 아닙니다.")

    token = authorization[len(BEARER_PREFIX) :].strip()
    if not token:
        raise TicketInvalidError("티켓이 비어 있습니다.")
    return token


def verify_ticket(token: str, secret: str, session_id: str) -> TicketPayload:
    """티켓을 검증하고 payload를 반환한다.

    Args:
        token: Bearer 토큰
        secret: `EXPMAP_TICKET_SECRET`
        session_id: 요청 경로의 `{session_id}`

    Returns:
        TicketPayload: 검증된 payload. `sub`를 user_id로 신뢰할 수 있다

    Raises:
        TicketInvalidError: 서명 불일치 또는 payload 형식 오류
        TicketExpiredError: 만료
        SessionForbiddenError: `sid`가 경로 `session_id`와 다름
    """
    try:
        # PyJWT가 서명 검증과 exp 검사를 상수 시간 비교(hmac.compare_digest)로 수행한다.
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TicketExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        # 서명 불일치·알고리즘 위조·형식 오류를 한 코드로 묶는다.
        # 어느 쪽인지 알려주면 공격자에게 단서가 된다.
        raise TicketInvalidError() from exc

    try:
        payload = TicketPayload(**claims)
    except Exception as exc:
        raise TicketInvalidError("티켓 payload 형식이 올바르지 않습니다.") from exc

    if payload.sid != session_id:
        logger.warning("티켓 sid 불일치 (sub=%s)", payload.sub)
        raise SessionForbiddenError()

    return payload
