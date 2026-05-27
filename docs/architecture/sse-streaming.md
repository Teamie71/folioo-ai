# FOLIOO AI - SSE 스트리밍 프로토콜

## 개요

NestJS 백엔드는 Folioo AI의 FastAPI SSE 스트림을 프록시하며, 클라이언트는 `event` 필드와 JSON `data.type` 값을 기준으로 이벤트를 처리합니다.

## 스트리밍 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/v1/interview/sessions/stream` | 인터뷰 세션 생성과 첫 질문 스트리밍 |
| `POST` | `/api/v1/interview/sessions/{session_id}/chat/stream` | 사용자 메시지 처리와 AI 응답 스트리밍 |
| `POST` | `/api/v1/interview/sessions/{session_id}/extend/stream` | 완료된 인터뷰의 연장 모드 첫 질문 스트리밍 |

## 응답 헤더

| 헤더 | 값 | 설명 |
|---|---|---|
| `Content-Type` | `text/event-stream` | SSE 스트림 |
| `Cache-Control` | `no-cache` | 프록시/브라우저 캐시 방지 |
| `X-Accel-Buffering` | `no` | Nginx 프록시 버퍼링 비활성화 |
| `X-Session-Id` | UUID 문자열 | 세션 생성 스트림에서 생성된 세션 ID 반환 |

## 이벤트 규격

| 이벤트명 | data 구조 | 설명 |
|---|---|---|
| `content_block_delta` | `{"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}` | LLM 토큰 스트리밍 |
| `retriever_status` | `{"type":"retriever_status","message":"..."}` | 유사 인사이트 조회 시작/상태 안내 |
| `retriever_result` | `{"type":"retriever_result","insights":[...]}` | 유사 인사이트 조회 결과 |
| `message_complete` | `{"type":"message_complete","message":{...}}` | 스트리밍 처리 완료와 최종 상태 |
| `error` | `{"type":"error","error":{"code":"...","message":"..."}}` | 오류 이벤트 |
| `ping` | `{"type":"ping","timestamp":"2026-02-06T..."}` | 연결 유지용 heartbeat. 10초 간격 |

## 완료 이벤트 payload

`message_complete`의 `message` 구조는 엔드포인트별로 일부 필드가 다릅니다.

| 엔드포인트 | 주요 필드 |
|---|---|
| 세션 생성 스트림 | `session_id`, `first_question`, `status`, `current_stage`, `stage_progress` |
| 채팅 스트림 | `ai_response`, `status`, `current_stage`, `stage_progress`, `overall_completion`, `all_complete` |
| 연장 모드 스트림 | `ai_response`, `status`, `current_stage`, `stage_progress`, `overall_completion`, `all_complete` |

공통으로 연장 모드 메타데이터인 `is_extended_mode`, `extension_turns_used`, `extension_turns_max`가 포함될 수 있습니다.

## 에러 코드

| 코드 | 설명 |
|---|---|
| `session_not_found` | 요청한 인터뷰 세션을 찾을 수 없음 |
| `final_state_missing` | 스트리밍 완료 후 최종 LangGraph 상태 조회 실패 |
| `llm_error` | LLM 또는 그래프 처리 중 오류 발생 |
| `stream_event_error` | 업스트림 SSE 이벤트 처리 중 예외 발생 |
| `invalid_stream_event` | 내부 스트림 이벤트 포맷이 올바르지 않음 |

## SSE 원본 예시

```text
event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"안녕"}}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"하세요"}}

event: message_complete
data: {"type":"message_complete","message":{"ai_response":"안녕하세요","current_stage":1,"stage_progress":{},"overall_completion":15.0,"all_complete":false}}
```

## 프록시 처리 원칙

- NestJS 백엔드는 FastAPI의 `event` 이름을 변경하지 않습니다.
- `data`는 UTF-8 JSON 문자열로 전달합니다.
- `ping`은 비즈니스 이벤트가 아니므로 연결 유지와 타임아웃 방지에만 사용합니다.
- `error` 이벤트를 받으면 스트림을 정상 종료하거나 클라이언트에 오류 상태를 전파합니다.
- Nginx 등 중간 프록시가 있으면 응답 버퍼링을 비활성화해야 토큰 단위 스트리밍이 유지됩니다.
