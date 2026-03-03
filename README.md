# SSE 이벤트 프로토콜

NestJS 백엔드가 FastAPI SSE 스트림을 프록시할 때, 아래 이벤트 규격으로 파싱합니다.

| 이벤트명 | data 구조 | 설명 |
|----------|-----------|------|
| `content_block_delta` | `{"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}` | LLM 토큰 스트리밍 |
| `message_complete` | `{"type":"message_complete","message":{"ai_response":"...","current_stage":1,...}}` | 처리 완료 |
| `error` | `{"type":"error","error":{"code":"...","message":"..."}}` | 에러 |
| `ping` | `{"type":"ping","timestamp":"2026-02-06T..."}` | 연결 유지 (10초 간격) |

SSE 원본 예시:

```text
event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"안녕"}}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"하세요"}}

event: message_complete
data: {"type":"message_complete","message":{"ai_response":"안녕하세요","current_stage":1,"stage_progress":{...},"overall_completion":15.0,"all_complete":false}}
```
