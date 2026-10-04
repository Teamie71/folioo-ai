-- 경험정리 AI 런타임이 공유 운영 DB에서 사용하는 스키마 보강
--
-- 메인 서버의 20260807220000_create_block_tree_schema.sql 적용 후 실행한다.
-- 기존 서버 테이블의 소유권은 유지하고, AI가 직접 사용하는 컬럼·테이블만 추가한다.

BEGIN;

ALTER TABLE ai_experience_request
  ADD COLUMN IF NOT EXISTS owner_token uuid;

ALTER TABLE ai_experience_request
  ADD COLUMN IF NOT EXISTS fallback_message text;

-- 세션 하나에서 두 worker가 동시에 실행권을 얻지 못하게 한다.
-- 이미 세션별 running 행이 중복돼 있다면 인덱스 생성이 실패하므로 먼저 데이터를
-- 점검해야 한다. 중복 행을 임의로 완료/실패 처리하지 않는다.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_experience_request_running
  ON ai_experience_request(session_id)
  WHERE status = 'running';

-- 대화 원문과 AI 응답은 AI 서버가 소유한다.
CREATE TABLE IF NOT EXISTS ai_experience_message (
  id            bigserial PRIMARY KEY,
  user_id       integer NOT NULL,
  session_id    uuid NOT NULL,
  request_id    uuid NOT NULL,
  user_message  text,
  ai_responses  jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (user_id, session_id)
    REFERENCES ai_experience_session(user_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_experience_message_session
  ON ai_experience_message(session_id, id);

COMMIT;
