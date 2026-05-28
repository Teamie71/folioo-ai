# FOLIOO 시각화 — 시각화 워커 실행 환경 및 큐 핸들러 패턴

> 이 문서는 `pptx-gen-plan-v6.md` 의 **§7.0.1** 과 **§7.0.2** 를 분리한 것이다.
> 절 번호(§7.0.1, §7.0.2)는 원본 문서와의 교차참조 유지를 위해 그대로 둔다.
> 작업 큐 + 워커 아키텍처 전체 그림은 `pptx-gen-plan-v6.md` §7.0,
> soffice 사양·운영(§8.3)과 인프라 구조(§8)는 `worker-spec.md` 참조.

---

### 7.0.1 시각화 워커 실행 환경 — Cloud Run Service (확정)

soffice 실행 + LLM I/O + 디스크 작업이 핵심 워크로드다.
"긴 처리 시간 + 메모리 + 파일 시스템 + 적절한 동시성"이 필요하고,
**Cloud Tasks 는 Push 방식이므로 워커는 HTTPS 엔드포인트를 노출하는 컨테이너**여야 한다.
→ 이 조건들을 종합해 **시각화 워커는 GCP Cloud Run Service 로 확정한다.**

**확정 스택:**
- 큐: **GCP Cloud Tasks** (단일 큐, HTTP Push, OIDC 인증)
- 시각화 워커: **GCP Cloud Run Service** (HTTP 엔드포인트 노출, `concurrency = 1`)
- 이벤트 fan-out: **NestJS EventEmitter2** (메인 백엔드 단일 인스턴스 가정, 외부 브로커 없음)
- (선택) Redis: 세션 등 기존 용도가 있으면 그대로 사용 (본 시각화 기능은 Redis 불필요)

**Cloud Run Service 인 이유:**
- HTTP 엔드포인트 자동 노출 → Cloud Tasks 의 push URL 로 그대로 등록 (Push 방식과 정합)
- 작업을 요청 안에서 동기 처리(패턴 A, §7.0.2)하는 동안 요청이 열려 있어, Cloud Run 이
  in-flight 요청이 있는 인스턴스를 scale-down 으로 죽이지 않고 **작업 완료까지 보장**한다
  (반대로 "즉시 200 + 백그라운드 처리"는 응답 후 CPU throttle + scale-down 위험이 있어 채택 안 함)
- OIDC 토큰 검증을 Cloud Run IAM(`roles/run.invoker`)에 위임 가능
- 콜드스타트 비용 적고, 자동 스케일링(인스턴스 수), soffice 패키징 자유
- min-instances 0 (유휴 시 인스턴스 0개 → 유휴 비용 0; 콜드스타트는 비동기 백그라운드라 사용자 체감 작음, 첫 작업 지연 민감하면 1)

**인스턴스 재활용 정책 (N회 처리 후 종료):**
- soffice 는 메모리 누수 경향이 있으므로 변환마다 **별도 서브프로세스로 띄우고 종료**해
  1~2 GB 를 즉시 회수한다 (§8.3.3).
- 그 위에, 워커는 **누적 soffice 변환 N회(기본 20회, 보수적 시작값) 후 200 응답 직후 스스로 종료**하고
  Cloud Run 이 새 인스턴스를 띄워 잔여 누수를 리셋한다 (§8.3.4 / §8.3.8).
- N 값은 부하 테스트(§8.3.6)로 메모리 톱니파 피크가 한도 안에 들도록 정한다.
  Phase 1 초기 생성은 길고 Phase 2 재생성은 짧으므로(§8.2), 짧은 작업의 콜드스타트 비중을
  감안해 **작업마다 인스턴스를 종료하기보다 누적 변환 N회 기준으로 재활용**한다.

**시각화 워커 동시성 정책:**
- 인스턴스 당 동시 요청 수: **`concurrency = 1`** (soffice 메모리 누수 + 멀티스레드 안전성 약함).
  무거운 작업 1개가 인스턴스 1개를 점유하고, 처리량은 인스턴스를 가로로 늘려 확보한다.
- Cloud Tasks 의 `maxConcurrentDispatches` 로 전체 동시성(= 동시 인스턴스 수 = 총 메모리·LLM rate)을 제어
- 메인 백엔드의 큐 백로그 메트릭 기반 알람 (Cloud Tasks `oldest_age` > 5분 → 경고)
- 최대 워커 인스턴스 수는 LLM API rate limit 고려해서 설정 (예: max 20)

**운영 관측성:**
- 워커는 `/metrics` 에 Prometheus text exposition 형식으로 soffice duration/RSS,
  timeout/OOM/other 실패, tmp 디스크 사용량, 폰트 fallback, lifetime 처리 수를 노출한다.
- `worker_jobs_processed_total` 은 `/health.lifetime_processed` 와 같은 카운터를 읽고,
  `worker_ready_for_recycle` 은 `/health.ready_for_recycle` 과 같은 기준으로 0/1 gauge 를 노출한다.
- 알람 기준과 PromQL 예시는 `worker-spec.md` §8.3.7 에 둔다.

> **코드 실행 모델 / 강격리 샌드박스(Daytona 등)는 본 MVP 범위 밖이다 — §17 참조.**
> 현재 설계는 LLM 이 데이터(`fills`)를 내고 결정적 코드가 적용하는 방식이라 임의 코드 실행이
> 없으므로, Cloud Run 의 프로세스 격리 + 컨테이너 하드닝으로 충분하다. 원조 Anthropic PPTX
> 스킬처럼 LLM 이 코드를 작성·실행하는 모델로 전환할 때 비로소 강격리가 필요해진다.

### 7.0.2 Cloud Tasks Push 핸들러 패턴 (워커 측)

Cloud Tasks 는 push 요청에 대해 핸들러의 응답을 기다리며, 응답 없이
`dispatchDeadline` 을 초과하면 실패로 간주한다.

**MVP 는 패턴 A (요청 안에서 동기 처리) 를 채택한다.**
- Push 요청을 받으면 **그 요청 안에서 파이프라인 전체를 동기적으로 실행**한다.
  Cloud Run 의 request timeout 은 최대 60분까지 설정 가능하다 (본 문서는 30분 — §8.3.8).
- 실행 도중 워커 → 메인 콜백으로 중간 이벤트를 발신한다
  (slide_plan_ready, slide_content_ready 등).
- 모든 처리가 끝나면 Cloud Tasks 에 200 OK 로 응답한다.
- 장점: 단순 — 별도 백그라운드 잡 시스템이 필요 없다.

**작업 시간 상한 — 30분 (명시적 정책):**
- `dispatchDeadline` 의 최대값이 30분이므로 이를 단일 작업의 상한으로 둔다 (§11.2.0).
- 한 작업이 30분 안에 끝나지 못하면 **그대로 실패로 처리한다.** 더 긴 작업을 위한
  별도 경로(작업 분할 등)는 MVP 범위에 두지 않는다.
- 30분을 초과해 워커가 콜백을 멈추면, 해당 슬라이드는 메인의 stuck 복구
  크론(§7.4.4)이 `error` 로 정리한다.

> **패턴 B (즉시 200 OK + 워커 내부 백그라운드 처리)** 는 30분으로도 부족한
> 장시간 작업(예: 영상 변환)이 추가될 때 검토한다. MVP 에서는 사용하지 않는다.
