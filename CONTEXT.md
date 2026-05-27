# folioo-ai — 컨텍스트

`folioo-ai` 는 FOLIOO 서비스의 **AI 워크로드 모노레포**다. 같은 코드베이스에서 **인터뷰 챗 서비스**와 **시각화 워커** 두 Cloud Run 서비스를 빌드·배포한다 (ADR-0001). 사용자向 트래픽과 DB 는 별도 레포의 **메인 백엔드(NestJS)** 가 단독 소유한다.

이 글로서리는 두 서브도메인 용어를 한 파일에 담는다 — **① 인터뷰 챗(추가 대화)** 와 **② PPTX 시각화**. 둘은 언어·런타임이 다르다. 시각화 워커 코드(`apps/pptx-worker/`)가 생기면 **CONTEXT-MAP.md** 로 분리한다.

## ① 인터뷰 챗 — 추가 대화 (features/interview/)

### 추가 대화

정규 경험정리 인터뷰의 11개 고정 질문이 모두 끝난 뒤, 사용자가 선택해서 진행하는 보완 대화다. 기존 코드의 내부 명칭은 `extension`을 유지하지만, 제품 언어에서는 추가 대화라고 부른다.

### 추가 질문 target

추가 대화에서 보완 여부를 판단하고 질문 순서를 정하는 단위다. `target` id가 상태 추적의 canonical key이며, `field_name`은 해당 target이 어느 수집 필드에 연결되는지 나타내는 참조다. 하나의 수집 필드 안에 여러 target이 있을 수 있으므로, 사용자에게는 내부 field name이 아니라 사람이 이해할 수 있는 라벨과 질문 의도로 표현한다.

### 사전 판정

추가 대화를 시작할 때 기존 11개 고정 질문 답변만 기준으로 각 추가 질문 target이 이미 충분한지 판단하는 단계다. 사전 판정은 전체 target을 한 번의 LLM structured output으로 판정하며, 반환되지 않은 target이나 판정 실패 target은 보수적으로 부족한 상태로 둔다. 추가 대화 중 사용자의 후속 답변을 반영해 target 충족 여부를 갱신하는 일은 별도 흐름으로 다룬다.

### 1차 패스

사전 판정에서 부족하다고 판단된 target 중 아직 한 번도 질문하지 않은 target을 우선순위대로 묻는 단계다. 조건은 `asked_count == 0`이고 `is_satisfied == false`인 target이다.

### 2차 패스

1차 패스 후보가 모두 소진되고 질문 예산이 남았을 때, 한 번 질문했지만 아직 충분하다고 판정되지 않은 target을 우선순위대로 한 번 더 묻는 단계다. 조건은 `asked_count == 1`이고 `is_satisfied == false`인 target이며, 같은 target은 최대 2회까지만 질문한다.
---

## ② PPTX 시각화 (apps/pptx-worker — 기획 중)

### Language

#### 서비스 / 컴포넌트

**메인 백엔드 (Main Backend)**:
NestJS 로 작성된 사용자向 API 서버. 인증·세션·Postgres·SSE·signed URL 발급·Cloud Tasks enqueue 를 단독 소유한다. **본 모노레포 외부의 별도 레포** 로 운영된다.
_Avoid_: 메인 서버, API 서버, BFF

**AI 서버 (folioo-ai)**:
본 모노레포 자체. **코드 저장소 / 빌드 단위** 의 이름이며 런타임 서비스 한 개를 가리키지 않는다.
_Avoid_: AI 워커, AI Worker (런타임을 가리킬 때는 아래 두 서비스 이름을 사용)

**인터뷰 챗 서비스 (Interview Chat Service)**:
포트폴리오 인터뷰 챗을 위한 Cloud Run 서비스. LLM 토큰을 사용자에게 직접 SSE 로 스트리밍한다. **PPTX 시각화에는 관여하지 않는다.**
_Avoid_: 인터뷰 워커

**시각화 워커 (PPTX Worker / Visualization Worker)**:
PPTX 시각화 전용 Cloud Run 서비스. Cloud Tasks 의 HTTP Push 를 받아 LLM 호출 + OOXML 편집 + soffice/pdftoppm 실행 + GCS R/W 를 수행하고, 진행 이벤트는 메인 백엔드 콜백(`/api/internal/...`) 으로 전달한다. **DB / SSE 에 직접 접근하지 않는다.**
_Avoid_: AI 서버 (인수 인계 v1 까지 쓰이던 표현 — 이제 폐기), AI Worker, 워커 (문서 안에서만 단축어로 사용 가능)

**프론트엔드 (Frontend)**:
웹 클라이언트. 메인 백엔드의 REST + SSE 만 호출하며 시각화 워커를 직접 호출하지 않는다.

#### 인프라

**큐 (Queue)**:
GCP Cloud Tasks 단일 큐 `viz-jobs` 를 가리킨다 (ADR-0002). `messageType` 필드로 작업 종류를 분기한다.

**오브젝트 스토어 (Object Store)**:
GCS 단일 버킷 `folioo-visualizations` 를 가리킨다 (full GCP 로 확정 — ADR-0001/0002). 메인 백엔드는 **signed URL 발급 전용**, 시각화 워커는 **IAM 기반 직접 R/W**.

#### 도메인 모델

**Job (시각화 작업)**:
한 포트폴리오에 대해 사용자가 시작한 시각화 생성 요청 한 건. DB 테이블 `visualization_jobs` 의 row 1개와 1:1 대응한다. **한 포트폴리오당 단 하나의 시각화 Job만 생성**할 수 있다 (1:1 관계). 시각화가 완료된 후 원본 포트폴리오 텍스트를 수정해도 시각화 결과는 갱신되지 않고 기존 완성본을 유지한다. 하나의 Job 안에서 초기 생성·슬라이드별 재생성 등 여러 Cloud Tasks task 가 발생할 수 있다 (1:N).
_Avoid_: "작업" 단독 사용 (Cloud Tasks task 와 혼동), "시각화 요청" (HTTP request 와 혼동)

**Template**:
하나의 색상 조합(디자인 시스템)을 공유하는 Source Slide 묶음. GCS 의 `templates/{template_id}/` 폴더 1개 = 하나의 Template. `template.pptx`(30~40장의 Source Slide 풀) + `meta.json` + `thumbnail.jpg` 로 구성된다. DB 에서는 `visualization_jobs.template_id` 로 참조된다.
_Avoid_: "테마" (PowerPoint 의 Theme/Master 표준 용어와 충돌), "디자인" (너무 범용)

**Slot**:
하나의 Source Slide 안에서 편집 가능한 도형. `SlideEditor.extract_slots()` 가 슬라이드 XML 의 `cNvPr/@id`·위치·크기·현재 텍스트·폰트 크기로 자동 기술한다. LLM 에게 "여기에 뭘 넣을지 결정해" 라고 제시되는 빈 칸 단위.
_Avoid_: Placeholder (PowerPoint 마스터 레이아웃의 표준 placeholder 와 충돌)

**Fill**:
하나의 Slot 에 대한 LLM 의 콘텐츠 결정. `action` 으로 `text` / `remove` / `chart` 를 지정한다. DB 에 `visualization_slides.current_fills` 로 보존되며, 콜백·SSE·재생성 입력에서 `currentFills` 로 참조된다.
_Avoid_: "콘텐츠 매핑", "슬롯 값"

#### 슬라이드

본 도메인 글로서리에서 한국어 본문이든 영문 본문이든 항상 **영문 표기 (Slide / Source Slide / Source Slide 카테고리)** 를 그대로 사용한다.

**Slide**:
사용자가 만든 프레젠테이션 안의 슬라이드 한 장. DB 테이블 `visualization_slides` 의 row 1개와 1:1 대응한다. SSE 이벤트 `slide_*`, URL path `{slide_id}` 가 모두 이것을 가리킨다.
_Avoid_: Output Slide, Project Slide (Slide 가 기본 단어이며 prefix 없이 쓴다)

**Source Slide**:
디자이너가 만든 템플릿 PPTX 풀 안의 슬라이드 한 장. `meta.json` 의 `slides[]` 한 엔트리 = 한 Source Slide 다. `id = "cover_B"` 같이 알파벳 식별자를 가지며, DB 에는 `visualization_slides.source_slide_id` 로 참조된다. Step 1 LLM 출력의 `selected_slide_id` 도 결국 한 Source Slide 의 id 를 가리킨다.
_Avoid_: Template Slide (Template 이라는 단어가 색 조합 단위 blue/green/dark 와 충돌), Layout (PowerPoint 의 마스터 슬라이드 레이아웃이라는 PPT 표준 용어와 충돌), "슬라이드 풀" (단위가 아닌 집합 표현일 뿐)

**Source Slide 카테고리**:
모든 템플릿이 공유하는 글로벌 표준 enum 으로 한 Source Slide 의 역할을 분류. cover / toc / overview / problem / process / outcome / chart / visual / text / closing (v5 §3.3).
_Avoid_: 슬라이드 카테고리 (Slide 의 카테고리가 아니다 — 항상 Source Slide 의 카테고리)

#### 사이클 / 단계

**Phase**:
한 시각화 작업이 거치는 **사용자 액션 사이클 단계**. v5 §5 의 Phase 1 / 2 / 3 = 초기 생성 / 확인 & 수정 / 내보내기 에 1:1 대응한다.
_Avoid_: Stage, Step (이 둘은 다른 의미를 가진다 — 아래 항목 참조)

**Pipeline Stage**:
**시각화 워커 내부에서 한 작업이 진행되는 파이프라인 단계**. SSE 이벤트 `pipeline_stage_changed` 의 `pipelineStage` 필드로 표현되며 값은 `contentGenerating` / `rendering` / `completed` 다. 워커의 Step 1~7 묶음을 사용자에게 보여줄 때 사용한다.
_Avoid_: phase (Phase 와 헷갈림 — v5 의 SSE 이벤트 `phase_changed` / payload key `phase` 는 본 항목에 맞춰 **`pipeline_stage_changed` / `pipelineStage` 로 개명**한다)

**~~UX Phase A / B~~**:
v5 §2.2 의 표현 — **폐기**. UI 문구("AI가 콘텐츠를 구성하고 있어요" 등) 는 디자이너 가이드 수준으로만 남기고, 도메인 용어로는 항상 **Pipeline Stage** 값을 사용한다.

### Relationships

- 한 **시각화 작업** 은 **시각화 워커** 에서 처리되며 진행 이벤트는 **메인 백엔드** 로만 콜백된다
- **메인 백엔드** 는 **Postgres / SSE / signed URL 발급** 을 단독 소유한다
- **시각화 워커** 는 **DB 에 직접 접근하지 않는다** — 필요한 컨텍스트는 메인의 `/api/internal/...` 으로 조회한다
- **인터뷰 챗 서비스** 와 **시각화 워커** 는 같은 모노레포(**AI 서버 / folioo-ai**) 에서 빌드되지만 **별도 Cloud Run 서비스** 로 배포된다
- **프론트엔드** 는 **메인 백엔드** 만 호출하며 워커·인터뷰 챗 서비스를 직접 호출하지 않는다
- 한 사용자 작업은 **Phase 1 (초기 생성)** 에서 **Pipeline Stage** 를 `contentGenerating` → `rendering` → `completed` 순으로 거친다 — Phase 와 Pipeline Stage 는 **서로 다른 축**이며 같은 단어로 부르지 않는다
- 한 **Slide** 는 정확히 하나의 **Source Slide** 를 원본으로 가진다 (`source_slide_id`)
- 한 **Source Slide** 는 정확히 하나의 **Source Slide 카테고리** 에 속한다

### Example dialogue

> **Dev**: "**시각화 워커** 가 LLM 응답 받은 다음에 어디로 보내야 하지?"
> **Domain expert**: "**메인 백엔드** 의 `/api/internal/visualizations/{job_id}/slides/{slide_id}/events` 로 콜백. 워커는 DB 도, 프론트向 SSE 도 직접 못 건드려."
> **Dev**: "그러면 **AI 서버** 가 SSE 푸시하는 건 인터뷰 챗 얘기지?"
> **Domain expert**: "**AI 서버** 는 레포 이름이라 그 말은 모호해. **인터뷰 챗 서비스** 만 사용자에게 직접 SSE 를 노출하고, **시각화 워커** 는 안 한다."

### Flagged ambiguities

- v1 `main-backend-handoff.md` 에서 "AI 서버" 는 LLM·OOXML·soffice 까지 모두 처리하는 단일 FastAPI 서비스를 의미했음 — **본 CONTEXT 기준으로 폐기**. PPTX 워크로드를 가리킬 때는 항상 **시각화 워커** 라고 부른다.
- v5 문서가 §11.2 에서 "워커 코드베이스(별도 레포 또는 folioo-ai 의 별도 서비스)" 로 모호하게 적은 부분은 ADR-0001 에 의해 **folioo-ai 모노레포의 `apps/pptx-worker/`** 로 확정됨.
- v5 안에서 "Phase" 가 ① 사용자 사이클 단계(§5) ② UX 단계 A/B(§2.2) ③ SSE `phase_changed` 의 파이프라인 단계(§7.1) 세 가지로 혼용됨 — 본 CONTEXT 기준으로 ①만 **Phase** 로 유지, ③은 **Pipeline Stage** 로 분리(SSE 이벤트도 `pipeline_stage_changed` 로 개명), ②는 폐기.
- v5 본문에서 "슬라이드" / "slide" 가 ① 템플릿 풀의 슬라이드 한 장(§3) ② 사용자가 생성한 슬라이드 한 장(§10.3, §11) 을 동시에 가리킴 — 본 CONTEXT 기준 ①은 **Source Slide**, ②는 **Slide** 로 분리. DB 컬럼 `source_slide_id` / `slide_order` 명명과 일치한다.
