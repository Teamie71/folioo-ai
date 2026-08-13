-- 경험정리 로컬 개발용 스키마
--
-- 출처: docs/architecture/experience-map-api-spec.md 3-1 ~ 3-4
--
-- ⚠️ 이 파일은 **로컬 개발 전용**이다. 운영 스키마는 메인 서버 migration 이
-- 소유하며(외부-A), 이 파일은 그것을 대신하지 않는다. 메인 migration 이 나오면
-- 대조해서 차이를 없애야 한다.
--
-- 운영 migration 과의 차이:
--   ai_experience_request.owner_token — 명세 3-3 에 없다. AGENT_HANDOFF.md 참고.
--   메인 서버 migration 에 이 컬럼이 없으면 운영에서 UndefinedColumnError 가 난다.
--
-- 포함하지 않는 것: block, block_kind
--   명세에 DDL 이 없고 조회 쿼리(4-1)와 제약(3-5)만 있다. 추측해서 만들면
--   실제 스키마와 어긋나므로 넣지 않는다. 3.05(경험 맵 Repository) 착수 전에
--   메인 서버에서 두 테이블 DDL 을 받아야 한다.

BEGIN;

-- ===== 3-1. experience_map =====
-- 낙관적 잠금 대상. 운영에서는 메인 서버만 쓰고 AI 서버는 SELECT 만 한다.
CREATE TABLE IF NOT EXISTS experience_map (
  user_id      bigint PRIMARY KEY,
  map_version  bigint NOT NULL DEFAULT 1,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ===== 3-2. ai_experience_session =====
-- 사용자당 세션 1개. LangGraph thread_id = session_id.
CREATE TABLE IF NOT EXISTS ai_experience_session (
  user_id      bigint PRIMARY KEY,
  session_id   uuid NOT NULL UNIQUE,
  active_gap   jsonb,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, session_id)
);

-- ===== 3-3. ai_experience_request =====
-- API 상태의 유일한 기준. checkpoint status 를 상태로 쓰지 않는다.
CREATE TABLE IF NOT EXISTS ai_experience_request (
  user_id             bigint NOT NULL,
  session_id          uuid NOT NULL,
  request_id          uuid NOT NULL,
  request_hash        char(64) NOT NULL,
  status              varchar(16) NOT NULL,
  failed_node         varchar(64),
  retryable           boolean NOT NULL DEFAULT false,
  retry_expires_at    timestamptz,
  lease_expires_at    timestamptz,
  -- ⚠️ 명세 3-3 에 없는 컬럼이다. 운영 migration 에 반드시 함께 넣어야 한다.
  -- 실행권을 가진 worker 를 식별한다. 이 값이 맞아야 lease 갱신·완료·실패가
  -- 반영된다. 없으면 lease 를 잃은 worker 가 다른 worker 의 결과를 덮는다.
  --
  -- **nullable 이어야 한다.** NOT NULL 로 두면 안 된다 — 완료·실패·만료 정리가
  -- 실행권을 회수할 때 NULL 로 되돌린다. NULL 인 행은 어떤 token 과도 맞지 않아
  -- 상태를 바꿀 수 없고(= 검사를 우회하지 않고), 만료 정리가 풀어 준다.
  owner_token         uuid,
  base_map_version    bigint,
  committed_version   bigint,
  input_meta          jsonb NOT NULL DEFAULT '{}'::jsonb,
  result              jsonb,
  suggestion          jsonb,
  error               jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, request_id),
  FOREIGN KEY (user_id, session_id)
    REFERENCES ai_experience_session(user_id, session_id),
  CHECK (status IN ('running', 'completed', 'failed'))
);

-- 세션당 running 요청은 1건뿐이다. 동시 실행을 DB 가 막는다.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_experience_request_running
  ON ai_experience_request(session_id)
  WHERE status = 'running';

-- ===== 3-4. ai_commit_log =====
-- 되돌리기용 역연산 기록. **메인 서버 단독 소유**이며 AI 서버 계정은 권한이 없다.
-- 로컬에서는 커밋 API mock 이 쓸 수 있게 만들어 둔다.
CREATE TABLE IF NOT EXISTS ai_commit_log (
  user_id           bigint PRIMARY KEY,
  request_id        uuid NOT NULL,
  previous_version  bigint NOT NULL,
  committed_version bigint NOT NULL,
  created_block_ids bigint[] NOT NULL,
  updated_blocks    jsonb NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now()
);

COMMIT;
