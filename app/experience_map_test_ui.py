"""경험정리 수동 검증용 내부 테스트 UI.

운영 프론트의 대체물이 아니다. 메인 서버가 아직 티켓을 발급하지 않는 개발 환경에서
실제 LLM·SSE·DB 흐름을 확인하기 위한 도구이며,
`EXPERIENCE_MAP_TEST_UI_ENABLED=true`일 때만 앱에 등록한다.
"""

import os
import secrets
import time

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.schemas.experience_map import CreateSessionRequest
from features.experience_map.service import get_service

router = APIRouter(prefix="/experience-map/test", tags=["experience-map-test"])

_TICKET_TTL_SECONDS = 1800


def _require_api_key(request: Request) -> None:
    """테스트 세션 발급 요청의 API 키를 검증한다."""
    expected = os.getenv("AI_SERVICE_API_KEY", "")
    provided = request.headers.get("X-API-Key", "")
    if not expected or not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _issue_test_ticket(user_id: str, session_id: str) -> str:
    """테스트 UI에서만 쓰는 짧은 세션 티켓을 발급한다."""
    secret = os.getenv("EXPMAP_TICKET_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="EXPMAP_TICKET_SECRET is not configured",
        )

    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "sid": session_id, "iat": now, "exp": now + _TICKET_TTL_SECONDS},
        secret,
        algorithm="HS256",
    )


@router.get("", include_in_schema=False, response_class=HTMLResponse)
async def test_page() -> HTMLResponse:
    """브라우저에서 경험정리 흐름을 수동 검증하는 페이지를 반환한다."""
    return HTMLResponse(TEST_PAGE_HTML)


@router.post("/session")
async def create_test_session(payload: CreateSessionRequest, request: Request) -> dict[str, str]:
    """테스트 페이지 전용 세션과 티켓을 만든다."""
    _require_api_key(request)
    session_id, session_status = await get_service().create_session(payload.user_id)
    return {
        "session_id": session_id,
        "status": session_status,
        "ticket": _issue_test_ticket(payload.user_id, session_id),
        "expires_in_seconds": str(_TICKET_TTL_SECONDS),
    }


TEST_PAGE_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>경험정리 테스트 콘솔</title>
  <style>
    :root { color-scheme: dark; font-family: ui-sans-serif, system-ui, sans-serif; }
    body { margin: 0; background: #101218; color: #edf0f7; }
    main { max-width: 980px; margin: 0 auto; padding: 28px 18px 48px; }
    h1 { font-size: 24px; margin: 0 0 8px; } p { color: #abb4c5; line-height: 1.55; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 14px; }
    section { background: #191d27; border: 1px solid #30384a; border-radius: 12px; padding: 16px; }
    h2 { font-size: 16px; margin: 0 0 14px; }
    label { display: grid; gap: 6px; font-size: 13px; color: #c7cedb; margin: 10px 0; }
    input, textarea, select { box-sizing: border-box; width: 100%; border: 1px solid #475168; border-radius: 8px; background: #10131a; color: #eef2ff; padding: 10px; font: inherit; }
    textarea { min-height: 104px; resize: vertical; }
    button { border: 0; border-radius: 8px; padding: 10px 13px; margin: 5px 6px 0 0; background: #6e8cff; color: #09132c; font-weight: 700; cursor: pointer; }
    button.secondary { background: #30394c; color: #e6ebf7; } button:disabled { opacity: .45; cursor: wait; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    #session { color: #8fe6b0; overflow-wrap: anywhere; font-size: 13px; }
    #events { min-height: 360px; max-height: 640px; overflow: auto; white-space: pre-wrap; background: #0b0d12; border: 1px solid #30384a; border-radius: 10px; padding: 14px; line-height: 1.45; font-size: 12px; }
    .runtime { border-left: 4px solid #6e8cff; background: #171c2a; border-radius: 8px; margin: 14px 0; padding: 11px 13px; font-size: 14px; }
    .note { font-size: 13px; } .error { color: #ff9a9a; } .ok { color: #8fe6b0; }
  </style>
</head>
<body>
<main>
  <h1>경험정리 테스트 콘솔</h1>
  <p>테스트 전용 화면입니다. 세션·티켓은 브라우저 메모리에만 보관되며 페이지를 새로고침하면 사라집니다.</p>
  <div id="runtime" class="runtime">서버 연결 상태를 확인하는 중입니다.</div>
  <div class="grid">
    <section>
      <h2>1. 테스트 세션</h2>
      <label>AI 서비스 API 키<input id="apiKey" type="password" value="demo-key" autocomplete="off"></label>
      <label>사용자 ID<input id="userId" value="9000001" inputmode="numeric"></label>
      <button id="createSession">세션 시작</button>
      <p id="session" class="note">세션을 시작하세요.</p>
    </section>
    <section>
      <h2>2. 경험 입력</h2>
      <label>경험 내용<textarea id="message">대학생 동아리에서 행사 신청 페이지를 만들고, 이탈률을 분석해 입력 단계를 줄였습니다.</textarea></label>
      <label>첨부 파일 (선택, 최대 3개)<input id="files" type="file" multiple></label>
      <label>화면<select id="view"><option value="map">map</option><option value="list">list</option></select></label>
      <button id="send" disabled>실제 LLM으로 정리</button>
      <button id="retry" class="secondary" disabled>마지막 요청 재시도</button>
      <button id="state" class="secondary" disabled>세션 상태 조회</button>
    </section>
  </div>
  <section style="margin-top:14px"><h2>3. SSE 이벤트·결과</h2><pre id="events">대기 중</pre></section>
</main>
<script>
const state = { sessionId: null, ticket: null, requestId: null };
const output = document.querySelector('#events');
const runtime = document.querySelector('#runtime');
const buttons = ['send', 'retry', 'state'].map(id => document.querySelector('#' + id));
const log = (value, className = '') => {
  const line = document.createElement('div'); line.textContent = value;
  if (className) line.className = className; output.append(line); output.scrollTop = output.scrollHeight;
};
const setBusy = busy => buttons.forEach(button => { button.disabled = busy || !state.sessionId; });
const authHeaders = () => ({ Authorization: `Bearer ${state.ticket}` });
const newRequestId = () => crypto.randomUUID();
const setRuntime = (message, className = '') => { runtime.textContent = message; runtime.className = `runtime ${className}`; };

async function checkHealth() {
  try {
    const response = await fetch('/health');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const health = await response.json();
    setRuntime(`서버 연결됨 · DB ${health.experience_map_db} · Checkpointer ${health.checkpointer} · 세션을 시작하세요.`, 'ok');
  } catch (error) { setRuntime(`서버 연결 실패: ${error.message}`, 'error'); log(`서버 상태 조회 오류: ${error.message}`, 'error'); }
}

async function readSse(response) {
  if (!response.ok) throw new Error(await response.text());
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/); buffer = blocks.pop();
    for (const block of blocks) {
      const event = block.match(/^event:\s*(.+)$/m)?.[1] || 'message';
      const data = block.match(/^data:\s*(.+)$/m)?.[1];
      if (!data) continue;
      try { log(`[${event}] ${JSON.stringify(JSON.parse(data), null, 2)}`); }
      catch { log(`[${event}] ${data}`); }
    }
  }
}

document.querySelector('#createSession').onclick = async () => {
  const button = document.querySelector('#createSession'); button.disabled = true;
  setRuntime('테스트 세션을 생성하는 중입니다.'); log('세션 생성 요청 전송');
  try {
    const response = await fetch('/experience-map/test/session', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': document.querySelector('#apiKey').value },
      body: JSON.stringify({ user_id: document.querySelector('#userId').value })
    });
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json(); state.sessionId = data.session_id; state.ticket = data.ticket; state.requestId = null;
    document.querySelector('#session').textContent = `세션 준비됨: ${data.session_id} (티켓 ${data.expires_in_seconds}초)`;
    document.querySelector('#session').className = 'note ok'; output.textContent = ''; log('세션 생성 완료', 'ok'); setBusy(false);
    setRuntime('세션 준비 완료 · 실제 LLM으로 정리 버튼을 누르면 SSE 이벤트가 여기에 표시됩니다.', 'ok');
  } catch (error) { document.querySelector('#session').textContent = `세션 생성 실패: ${error.message}`; document.querySelector('#session').className = 'note error'; setRuntime(`세션 생성 실패: ${error.message}`, 'error'); }
  finally { button.disabled = false; }
};

document.querySelector('#send').onclick = async () => {
  state.requestId = newRequestId(); setBusy(true); setRuntime(`실제 LLM 요청 진행 중 · request_id: ${state.requestId}`); log(`요청 시작: ${state.requestId}`, 'ok');
  let streamSucceeded = false;
  try {
    const form = new FormData(); form.append('request', JSON.stringify({ request_id: state.requestId, user_message: document.querySelector('#message').value, view: document.querySelector('#view').value }));
    for (const file of document.querySelector('#files').files) form.append('files', file);
    await readSse(await fetch(`/api/v1/experience-map/sessions/${state.sessionId}/chat/stream`, { method: 'POST', headers: authHeaders(), body: form }));
    streamSucceeded = true;
  } catch (error) { log(`오류: ${error.message}`, 'error'); setRuntime(`LLM 요청 실패: ${error.message}`, 'error'); }
  finally { setBusy(false); if (streamSucceeded) setRuntime(`요청 스트림 종료 · request_id: ${state.requestId}`, 'ok'); }
};

document.querySelector('#retry').onclick = async () => {
  if (!state.requestId) return log('재시도할 요청이 없습니다.', 'error'); setBusy(true); log(`재시도: ${state.requestId}`, 'ok');
  try { await readSse(await fetch(`/api/v1/experience-map/sessions/${state.sessionId}/retry/stream`, { method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: state.requestId }) })); }
  catch (error) { log(`오류: ${error.message}`, 'error'); } finally { setBusy(false); }
};

document.querySelector('#state').onclick = async () => {
  try { const response = await fetch(`/api/v1/experience-map/sessions/${state.sessionId}/state`, { headers: authHeaders() }); if (!response.ok) throw new Error(await response.text()); const data = await response.json(); log(`[session_state] ${JSON.stringify(data, null, 2)}`, 'ok'); setRuntime(`세션 상태: ${data.status}${data.active_request_id ? ` · 실행 중 요청: ${data.active_request_id}` : ''}`, 'ok'); }
  catch (error) { log(`상태 조회 오류: ${error.message}`, 'error'); }
};

log('테스트 콘솔 준비 완료 · 세션 시작 후 실제 LLM으로 정리를 실행하세요.');
checkHealth();
</script>
</body>
</html>"""

__all__ = ["router"]
