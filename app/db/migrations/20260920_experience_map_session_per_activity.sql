-- 세션을 사용자 단위에서 활동(block_id) 단위로 분리한다
--
-- 메인 서버 변경사항(2026-09-20): POST /sessions body에 block_id가 추가되고,
-- 티켓 JWT에도 bid 클레임이 추가된다. 배포 시 기존 유저 단위 세션은 전부
-- 폐기된다(메인 서버 공지 1항) — AI 서버 쪽 세션과 그 하위 요청·대화 기록도
-- 함께 정리한다.

BEGIN;

ALTER TABLE ai_experience_session
  ADD COLUMN IF NOT EXISTS block_id text;

-- 기존 유저 단위 세션은 활동에 묶여 있지 않아 새 모델에서 의미가 없다.
-- 메인 서버도 같은 배포에서 유저 단위 세션을 전부 폐기하므로 함께 지운다.
-- FOREIGN KEY 위반을 피하려면 하위 테이블부터 지워야 한다.
DELETE FROM ai_experience_message
 WHERE session_id IN (SELECT session_id FROM ai_experience_session WHERE block_id IS NULL);

DELETE FROM ai_experience_request
 WHERE session_id IN (SELECT session_id FROM ai_experience_session WHERE block_id IS NULL);

DELETE FROM ai_experience_session WHERE block_id IS NULL;

ALTER TABLE ai_experience_session
  ALTER COLUMN block_id SET NOT NULL;

ALTER TABLE ai_experience_session
  DROP CONSTRAINT IF EXISTS ai_experience_session_pkey;

ALTER TABLE ai_experience_session
  ADD CONSTRAINT ai_experience_session_pkey PRIMARY KEY (user_id, block_id);

COMMIT;
