-- 카카오톡 대화 턴 접수·실행 상태와 메시지 채널 구분
--
-- 카톡 턴은 접수(202) 뒤 백그라운드에서 실행된다. 재시작 뒤에도 조회·재개할 수 있게
-- 접수 내용과 전달 상태를 AI 서버가 직접 소유하는 테이블에 남긴다.

BEGIN;

CREATE TABLE IF NOT EXISTS ai_kakao_turn (
  request_id       uuid PRIMARY KEY,
  user_id          bigint NOT NULL,
  session_id       uuid NOT NULL,
  block_id         text NOT NULL,
  request_hash     varchar(64) NOT NULL,
  utterance        text NOT NULL,
  -- 1회용 콜백 URL. 전달 시도가 끝나면 즉시 NULL 로 비운다. 로그에는 남기지 않는다.
  callback_url     text,
  expires_at       timestamptz NOT NULL,
  -- 외부에 공개하는 상태. 콜백 시도가 정리된 뒤에만 종결 값이 된다.
  state            varchar(16) NOT NULL DEFAULT 'ACCEPTED',
  -- 턴 결과. 콜백 전달 전에 먼저 확정되며 state 와 분리해 보관한다.
  outcome          varchar(16),
  delivery_status  varchar(16) NOT NULL DEFAULT 'NOT_ATTEMPTED',
  -- 콜백 본문. 재시작 뒤에도 전달 시도를 이어갈 수 있게 남긴다.
  reply_payload    jsonb,
  -- 콜백 전송 소유권. 먼저 채운 쪽만 전송한다.
  delivery_claimed_at timestamptz,
  usage_reported   boolean NOT NULL DEFAULT false,
  complete_notified boolean NOT NULL DEFAULT false,
  worker_lease_expires_at timestamptz,
  worker_token     uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ai_kakao_turn_state_check CHECK (
    state IN ('ACCEPTED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'EXPIRED', 'COMMIT_UNKNOWN')
  ),
  CONSTRAINT ai_kakao_turn_delivery_check CHECK (
    delivery_status IN ('SUCCESS', 'FAIL', 'UNKNOWN', 'NOT_ATTEMPTED')
  )
);

-- 미완료 턴 복구 스캔용
CREATE INDEX IF NOT EXISTS idx_ai_kakao_turn_pending
  ON ai_kakao_turn(updated_at)
  WHERE state IN ('ACCEPTED', 'RUNNING', 'COMMIT_UNKNOWN') OR complete_notified = false;

-- 웹·카톡 메시지 구분. 기존 행은 모두 웹이다.
ALTER TABLE ai_experience_message
  ADD COLUMN IF NOT EXISTS channel varchar(8) NOT NULL DEFAULT 'WEB';

ALTER TABLE ai_experience_message
  DROP CONSTRAINT IF EXISTS ai_experience_message_channel_check;

ALTER TABLE ai_experience_message
  ADD CONSTRAINT ai_experience_message_channel_check CHECK (channel IN ('WEB', 'KAKAO'));

COMMIT;
