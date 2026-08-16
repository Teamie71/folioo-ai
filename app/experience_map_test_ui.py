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
from features.experience_map.test_runtime import get_test_map_store

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
async def create_test_session(payload: CreateSessionRequest, request: Request) -> dict:
    """테스트 페이지 전용 세션과 티켓을 만든다."""
    _require_api_key(request)
    session_id, session_status = await get_service().create_session(payload.user_id)
    return {
        "session_id": session_id,
        "status": session_status,
        "ticket": _issue_test_ticket(payload.user_id, session_id),
        "expires_in_seconds": str(_TICKET_TTL_SECONDS),
    }


@router.get("/map/{user_id}")
async def get_test_map(user_id: str, request: Request) -> dict:
    """테스트 UI가 수정 대상 블록을 고를 수 있게 샘플 맵을 반환한다."""
    _require_api_key(request)
    if not user_id.isdecimal():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid user_id"
        )
    return await get_test_map_store().display_map(user_id)


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
    .workspace { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(310px, .7fr); gap: 14px; align-items: start; }
    section { background: #191d27; border: 1px solid #30384a; border-radius: 12px; padding: 16px; }
    h2 { font-size: 16px; margin: 0 0 14px; }
    label { display: grid; gap: 6px; font-size: 13px; color: #c7cedb; margin: 10px 0; }
    input, textarea, select { box-sizing: border-box; width: 100%; border: 1px solid #475168; border-radius: 8px; background: #10131a; color: #eef2ff; padding: 10px; font: inherit; }
    textarea { min-height: 104px; resize: vertical; }
    button { border: 0; border-radius: 8px; padding: 10px 13px; margin: 5px 6px 0 0; background: #6e8cff; color: #09132c; font-weight: 700; cursor: pointer; }
    button.secondary { background: #30394c; color: #e6ebf7; } button:disabled { opacity: .45; cursor: wait; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    #session { color: #8fe6b0; overflow-wrap: anywhere; font-size: 13px; }
    .chat-header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
    .chat-header h2 { margin: 0; } .status-dot { color: #8fe6b0; font-size: 12px; }
    #chatHistory { height: 480px; overflow: auto; padding: 14px 4px; display: flex; flex-direction: column; gap: 10px; }
    .bubble { max-width: 82%; border-radius: 14px; padding: 11px 13px; white-space: pre-wrap; line-height: 1.5; font-size: 14px; }
    .bubble.user { align-self: flex-end; background: #526ed4; color: #fff; border-bottom-right-radius: 3px; }
    .bubble.assistant { align-self: flex-start; background: #293044; border-bottom-left-radius: 3px; }
    .bubble.system { align-self: center; background: transparent; color: #9eabc2; font-size: 12px; padding: 4px; text-align: center; }
    .bubble.error { align-self: flex-start; background: #482a35; color: #ffb6bd; }
    .composer { border-top: 1px solid #30384a; padding-top: 12px; }
    .composer textarea { min-height: 90px; }
    #events { min-height: 180px; max-height: 340px; overflow: auto; white-space: pre-wrap; background: #0b0d12; border: 1px solid #30384a; border-radius: 10px; padding: 14px; line-height: 1.45; font-size: 12px; }
    #mapTree { min-height: 260px; max-height: 510px; overflow: auto; white-space: pre-wrap; background: #0b0d12; border: 1px solid #30384a; border-radius: 10px; padding: 14px; line-height: 1.55; font-size: 12px; }
    .runtime { border-left: 4px solid #6e8cff; background: #171c2a; border-radius: 8px; margin: 14px 0; padding: 11px 13px; font-size: 14px; }
    .note { font-size: 13px; } .error { color: #ff9a9a; } .ok { color: #8fe6b0; }
    summary { cursor: pointer; color: #c7cedb; margin-top: 12px; } details[open] summary { margin-bottom: 8px; }
    @media (max-width: 760px) { .workspace { grid-template-columns: 1fr; } #chatHistory { height: 400px; } }
  </style>
</head>
<body>
<main>
  <h1>경험정리 테스트 콘솔</h1>
  <p>테스트 전용 화면입니다. 세션·티켓은 브라우저 메모리에만 보관되며 페이지를 새로고침하면 사라집니다.</p>
  <div id="runtime" class="runtime">서버 연결 상태를 확인하는 중입니다.</div>
  <div class="workspace">
    <section>
      <div class="chat-header"><h2>경험정리 에이전트</h2><span id="chatStatus" class="status-dot">● 세션 시작 전</span></div>
      <div id="chatHistory"><div class="bubble assistant">안녕하세요. 오른쪽 맵에서 수정할 블록을 고르고, 어떤 방향으로 고칠지 말해 주세요.</div></div>
      <div class="composer">
        <label>수정할 블록<select id="block" disabled><option>세션을 시작하면 샘플 맵을 불러옵니다.</option></select></label>
        <label>메시지<textarea id="message" placeholder="예: 이 문장을 문제 상황과 분석 근거가 드러나도록 더 구체적으로 수정해줘.">선택한 블록의 문장을 수치와 행동이 드러나도록 더 구체적으로 수정해줘.</textarea></label>
        <p class="note">Enter로 전송 · Shift+Enter로 줄바꿈</p>
        <label>첨부 파일 (선택, 최대 3개)<input id="files" type="file" multiple></label>
        <button id="send" disabled>보내기</button><button id="retry" class="secondary" disabled>재시도</button><button id="state" class="secondary" disabled>상태</button>
      </div>
      <details><summary>디버그 SSE 이벤트 보기</summary><pre id="events">대기 중</pre></details>
    </section>
    <section>
      <h2>테스트용 경험 맵</h2>
      <p class="note">블록 선택은 LLM의 활동 컨텍스트를 좁힙니다. 변경은 이 페이지의 메모리 맵에만 반영됩니다.</p>
      <pre id="mapTree">세션을 시작하면 샘플 맵을 불러옵니다.</pre>
      <details><summary>테스트 세션 설정</summary>
        <label>AI 서비스 API 키<input id="apiKey" type="password" value="demo-key" autocomplete="off"></label>
        <label>사용자 ID<input id="userId" value="9000001" inputmode="numeric"></label>
        <label>화면<select id="view"><option value="map">map</option><option value="list">list</option></select></label>
        <button id="createSession">새 테스트 세션</button>
        <p id="session" class="note">세션을 시작하세요.</p>
      </details>
    </section>
  </div>
</main>
<script>
const state = { sessionId: null, ticket: null, requestId: null };
const output = document.querySelector('#events');
const runtime = document.querySelector('#runtime');
const blockSelect = document.querySelector('#block');
const mapTree = document.querySelector('#mapTree');
const chatHistory = document.querySelector('#chatHistory');
const chatStatus = document.querySelector('#chatStatus');
const buttons = ['send', 'retry', 'state'].map(id => document.querySelector('#' + id));
const log = (value, className = '') => {
  const line = document.createElement('div'); line.textContent = value;
  if (className) line.className = className; output.append(line); output.scrollTop = output.scrollHeight;
};
const addMessage = (role, value) => { const bubble = document.createElement('div'); bubble.className = `bubble ${role}`; bubble.textContent = value; chatHistory.append(bubble); chatHistory.scrollTop = chatHistory.scrollHeight; };
const setChatStatus = value => { chatStatus.textContent = `● ${value}`; };
const setBusy = busy => buttons.forEach(button => { button.disabled = busy || !state.sessionId; });
const authHeaders = () => ({ Authorization: `Bearer ${state.ticket}` });
const newRequestId = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  // Tailscale IP를 HTTP로 열면 일부 브라우저에서 Web Crypto가 비활성화된다.
  // API가 요구하는 UUID 형식을 보장하는 대체값을 사용한다.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, char => {
    const random = Math.floor(Math.random() * 16);
    const value = char === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
};
const setRuntime = (message, className = '') => { runtime.textContent = message; runtime.className = `runtime ${className}`; };

async function refreshMap() {
  const response = await fetch(`/experience-map/test/map/${document.querySelector('#userId').value}`, { headers: { 'X-API-Key': document.querySelector('#apiKey').value } });
  if (!response.ok) throw new Error(await response.text());
  const map = await response.json(); blockSelect.textContent = ''; let tree = `map_version: ${map.map_version}\n`;
  for (const activity of map.activities) {
    const lines = activity.tree.split('\n'); tree += `\n${activity.tree}\n`;
    for (const line of lines) {
      const option = document.createElement('option'); option.textContent = `${activity.title} · ${line.trim().replace(/^\[[^\]]+\]\s*/, '')}`;
      option.dataset.activityId = activity.id; option.dataset.blockText = line.trim().replace(/^\[[^\]]+\]\s*/, ''); blockSelect.append(option);
    }
  }
  blockSelect.disabled = false; mapTree.textContent = tree;
}

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
      try { const payload = JSON.parse(data); log(`[${event}] ${JSON.stringify(payload, null, 2)}`); if (event === 'message_complete') addMessage('assistant', payload.message.ai_response); if (event === 'error') addMessage('error', `요청 실패: ${payload.error.message}`); if (event === 'node_status') setChatStatus(`${payload.node} ${payload.status}`); }
      catch { log(`[${event}] ${data}`); }
    }
  }
}

document.querySelector('#createSession').onclick = async () => {
  const button = document.querySelector('#createSession'); button.disabled = true;
  setRuntime('테스트 세션을 생성하는 중입니다.'); setChatStatus('세션 생성 중'); log('세션 생성 요청 전송');
  try {
    const response = await fetch('/experience-map/test/session', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': document.querySelector('#apiKey').value },
      body: JSON.stringify({ user_id: document.querySelector('#userId').value })
    });
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json(); state.sessionId = data.session_id; state.ticket = data.ticket; state.requestId = null;
    await refreshMap();
    document.querySelector('#session').textContent = `세션 준비됨: ${data.session_id} (티켓 ${data.expires_in_seconds}초)`;
    document.querySelector('#session').className = 'note ok'; output.textContent = ''; log('세션 생성 완료', 'ok'); addMessage('system', '새 테스트 세션이 준비됐습니다. 수정할 블록과 메시지를 선택해 주세요.'); setBusy(false); setChatStatus('대화 준비됨');
    setRuntime('세션 준비 완료 · 실제 LLM으로 정리 버튼을 누르면 SSE 이벤트가 여기에 표시됩니다.', 'ok');
  } catch (error) { document.querySelector('#session').textContent = `세션 생성 실패: ${error.message}`; document.querySelector('#session').className = 'note error'; setRuntime(`세션 생성 실패: ${error.message}`, 'error'); }
  finally { button.disabled = false; }
};

document.querySelector('#send').onclick = async () => {
  state.requestId = newRequestId(); setBusy(true); setChatStatus('에이전트가 생각 중'); setRuntime(`실제 LLM 요청 진행 중 · request_id: ${state.requestId}`); log(`요청 시작: ${state.requestId}`, 'ok');
  let streamSucceeded = false;
  try {
    const selected = blockSelect.selectedOptions[0];
    const prompt = selected ? `수정 대상 블록: ${selected.dataset.blockText}\n\n사용자 지시: ${document.querySelector('#message').value}` : document.querySelector('#message').value;
    addMessage('user', document.querySelector('#message').value); const form = new FormData(); form.append('request', JSON.stringify({ request_id: state.requestId, user_message: prompt, context_experience_id: selected?.dataset.activityId, view: document.querySelector('#view').value }));
    for (const file of document.querySelector('#files').files) form.append('files', file);
    await readSse(await fetch(`/api/v1/experience-map/sessions/${state.sessionId}/chat/stream`, { method: 'POST', headers: authHeaders(), body: form }));
    streamSucceeded = true;
  } catch (error) { log(`오류: ${error.message}`, 'error'); addMessage('error', `요청 실패: ${error.message}`); setChatStatus('오류 발생'); setRuntime(`LLM 요청 실패: ${error.message}`, 'error'); }
  finally { setBusy(false); if (streamSucceeded) { await refreshMap(); setChatStatus('대화 준비됨'); setRuntime(`요청 스트림 종료 · request_id: ${state.requestId} · 샘플 맵을 갱신했습니다.`, 'ok'); } }
};

document.querySelector('#message').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (!document.querySelector('#send').disabled) document.querySelector('#send').click();
});

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
