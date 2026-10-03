"""SSE 스트림에 ping 이벤트를 인터리빙하는 공용 유틸"""

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime

from sse_starlette.sse import ServerSentEvent

from .constants import SSEErrorCode, SSEEventType

logger = logging.getLogger(__name__)

# ping 전송 간격 (초)
_PING_INTERVAL_SECONDS = 10
_STREAM_END = object()


async def interleave_ping_events(stream):
    """SSE 스트림에 ping 이벤트를 인터리빙"""
    stream_iter = aiter(stream)
    stream_iter_aclose = getattr(stream_iter, "aclose", None)
    stream_iter_closed = False
    next_event_task = asyncio.create_task(anext(stream_iter, _STREAM_END))

    try:
        while True:
            done, _ = await asyncio.wait({next_event_task}, timeout=_PING_INTERVAL_SECONDS)

            if done:
                try:
                    event_data = next_event_task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("업스트림 SSE 스트림 처리 중 예외 발생")
                    yield ServerSentEvent(
                        event=SSEEventType.ERROR,
                        data=json.dumps(
                            {
                                "type": SSEEventType.ERROR,
                                "error": {
                                    "code": SSEErrorCode.STREAM_EVENT_ERROR,
                                    "message": "SSE 스트림 처리 중 오류가 발생했습니다.",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    )
                    break

                if event_data is _STREAM_END:
                    break
                if (
                    not isinstance(event_data, dict)
                    or "event" not in event_data
                    or "data" not in event_data
                ):
                    is_dict = isinstance(event_data, dict)
                    invalid_event_meta = {
                        "is_dict": is_dict,
                        "type": event_data.get("type") if is_dict else None,
                        "id": event_data.get("id") if is_dict else None,
                        "keys": list(event_data.keys()) if is_dict else None,
                        "payload_redacted": True,
                    }
                    logger.error("잘못된 SSE 이벤트 페이로드: %s", invalid_event_meta)
                    yield ServerSentEvent(
                        event=SSEEventType.ERROR,
                        data=json.dumps(
                            {
                                "type": SSEEventType.ERROR,
                                "error": {
                                    "code": SSEErrorCode.INVALID_STREAM_EVENT,
                                    "message": "SSE 이벤트 포맷이 올바르지 않습니다.",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    )
                    if callable(stream_iter_aclose):
                        try:
                            await stream_iter_aclose()
                        except Exception:
                            logger.warning(
                                "잘못된 SSE 이벤트 종료 처리 중 upstream 스트림 정리 실패",
                                exc_info=True,
                            )
                        finally:
                            stream_iter_closed = True
                    break

                yield ServerSentEvent(
                    event=event_data["event"],
                    data=event_data["data"],
                )
                next_event_task = asyncio.create_task(anext(stream_iter, _STREAM_END))
            else:
                # 타임아웃 -> ping 이벤트 전송 (next_event_task는 유지)
                yield ServerSentEvent(
                    event=SSEEventType.PING,
                    data=json.dumps(
                        {
                            "type": SSEEventType.PING,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                )
    finally:
        if not next_event_task.done():
            next_event_task.cancel()
            with suppress(asyncio.CancelledError):
                await next_event_task
        if not stream_iter_closed and callable(stream_iter_aclose):
            with suppress(Exception):
                await stream_iter_aclose()


__all__ = ["interleave_ping_events"]
