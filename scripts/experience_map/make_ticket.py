#!/usr/bin/env python3
"""경험정리 티켓 발급 (로컬 수동 테스트용)

운영에서는 메인 서버가 로그인 인증 뒤 발급한다 (API 명세 2-1). 로컬에는 메인
서버가 없으므로 같은 키로 직접 서명해 API 를 호출할 수 있게 한다.

사용법:
    uv run python scripts/experience_map/make_ticket.py --user-id 1
    uv run python scripts/experience_map/make_ticket.py --user-id 1 --curl

    # 만료·위조 테스트
    uv run python scripts/experience_map/make_ticket.py --user-id 1 --expires-in -1
    uv run python scripts/experience_map/make_ticket.py --user-id 1 --secret wrong-secret
"""

import argparse
import json
import os
import sys
import time
import uuid

try:
    import jwt
except ImportError:
    sys.exit("PyJWT 가 필요합니다: uv sync --dev")

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--user-id", default="1", help="티켓 sub (십진 문자열)")
    parser.add_argument("--session-id", help="티켓 sid. 생략하면 새로 만든다")
    parser.add_argument("--request-id", help="요청 UUID. 생략하면 새로 만든다")
    parser.add_argument(
        "--expires-in", type=int, default=300, help="만료까지 초. 음수면 만료된 티켓"
    )
    parser.add_argument("--secret", help="서명 키. 생략하면 EXPMAP_TICKET_SECRET")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--curl", action="store_true", help="바로 붙여 쓸 curl 명령 출력")
    args = parser.parse_args()

    secret = args.secret or os.getenv("EXPMAP_TICKET_SECRET", "")
    if not secret:
        sys.exit("EXPMAP_TICKET_SECRET 이 설정되지 않았습니다. .env 를 확인하세요.")

    session_id = args.session_id or str(uuid.uuid4())
    request_id = args.request_id or str(uuid.uuid4())

    now = int(time.time())
    ticket = jwt.encode(
        {"sub": args.user_id, "sid": session_id, "iat": now, "exp": now + args.expires_in},
        secret,
        algorithm="HS256",
    )

    if args.curl:
        body = json.dumps(
            {"request_id": request_id, "user_message": "결제 실패 문제를 해결한 내용을 정리해줘"},
            ensure_ascii=False,
        )
        print(
            f"curl -N -X POST '{args.base_url}/api/v1/experience-map/sessions/{session_id}/chat/stream' \\\n"
            f"  -H 'Authorization: Bearer {ticket}' \\\n"
            f"  -H 'Accept: text/event-stream' \\\n"
            f"  -F 'request={body};type=application/json'"
        )
        return 0

    print(
        json.dumps(
            {
                "ticket": ticket,
                "session_id": session_id,
                "request_id": request_id,
                "expires_in": args.expires_in,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
