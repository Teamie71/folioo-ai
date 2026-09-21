-- 진행 중인 경험정리 요청을 중지시키는 API를 위한 플래그 컬럼
--
-- 프론트 요청(2026-09-20 공지 5항). 실행 중인 worker가 lease 갱신 주기마다
-- 이 값을 확인해 스스로 멈춘다 — 즉시 반영되지 않고 최대 그 주기
-- (LEASE_RENEW_INTERVAL_SECONDS)만큼 늦게 반영된다.

BEGIN;

ALTER TABLE ai_experience_request
  ADD COLUMN IF NOT EXISTS cancel_requested boolean NOT NULL DEFAULT false;

COMMIT;
