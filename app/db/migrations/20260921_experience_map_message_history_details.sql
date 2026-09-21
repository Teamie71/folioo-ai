-- 대화 히스토리에 첨부파일·실패·되돌림 상태를 남긴다
--
-- 프론트 요청(2026-09-20 공지 5항). 지금까지는 성공한 턴만 히스토리에 남아
-- 실패한 턴은 조회에서 통째로 사라졌다.

BEGIN;

ALTER TABLE ai_experience_message
  ADD COLUMN IF NOT EXISTS attachments jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE ai_experience_message
  ADD COLUMN IF NOT EXISTS status varchar(16) NOT NULL DEFAULT 'completed';

ALTER TABLE ai_experience_message
  ADD COLUMN IF NOT EXISTS can_revert boolean;

ALTER TABLE ai_experience_message
  DROP CONSTRAINT IF EXISTS ai_experience_message_status_check;

ALTER TABLE ai_experience_message
  ADD CONSTRAINT ai_experience_message_status_check CHECK (status IN ('completed', 'failed'));

COMMIT;
