# 시각화 워커를 folioo-ai 모노레포의 별도 배포 단위로 둔다

Status: accepted

PPTX 시각화 워크로드(soffice 1~2GB 메모리 + concurrency=1 + 콜드스타트)는 인터뷰 챗(LLM 토큰 SSE 스트리밍, 가벼운 메모리)과 인프라 프로파일이 완전히 달라 **같은 컨테이너에 합치면 soffice OOM 한 번에 인터뷰 챗 SSE 까지 죽는다**. 반면 두 워크로드는 LLM 클라이언트·HTTP 클라이언트·envelope 파서를 비롯한 공통 모듈을 광범위하게 공유하므로, **별도 레포로 분리하면 사설 PyPI 패키지 동기화 부담이 코드 공유 이득을 깎아먹는다**.

결정: `folioo-ai` 모노레포 안에 `apps/pptx-worker/` 를 추가하여 **코드는 `common/`·`features/` 를 직접 import 로 공유하되 Cloud Run 서비스는 인터뷰 챗(`folioo-ai-interview`)과 시각화 워커(`folioo-ai-pptx-worker`)로 분리 배포**한다. 두 서비스는 각자 Dockerfile · 메모리 · concurrency · min-instances 를 독립적으로 가진다.

## Considered Options

- **A. 완전 별도 레포** — 강한 격리 / 사설 PyPI 패키지 운영 비용이 MVP 단계에 비해 과함
- **B. 모노레포 + 두 배포 단위** ← 채택
- **C. 같은 Cloud Run 서비스 안에 라우터만 추가** — soffice OOM 이 인터뷰 챗까지 죽임

## Consequences

- v5 문서 §1.4.0 / §11.2 의 "별도 레포 또는 folioo-ai 의 별도 서비스" 라는 모호한 문구는 **"folioo-ai 모노레포의 `apps/pptx-worker/`"** 로 못 박는다.
- `common/llm/`, `common/clients/`, `common/http_client/` 등은 PyPI 패키지로 추출하지 않는다 — 직접 import.
- 두 서비스의 의존성 (`pyproject.toml`) 은 한 곳에서 관리하되, Docker 빌드는 각자 필요한 모듈만 COPY 한다.
- 메인 백엔드(NestJS) 는 이 모노레포 외부의 별도 레포로 유지한다 (언어·런타임이 다르므로).
