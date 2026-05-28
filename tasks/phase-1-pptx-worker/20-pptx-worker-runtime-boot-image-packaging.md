---
id: "1.20"
phase: 1
title: "PPTX Worker 런타임 부팅 및 이미지 패키징 차단 해소"
spec: "specs/phase-1/10-cloud-run-deployment-config.md"
depends_on: ["1.01", "1.05", "1.06", "1.07", "1.10"]
blocks: ["1.26"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 1.20 — PPTX Worker 런타임 부팅 및 이미지 패키징 차단 해소

> Spec: [`specs/phase-1/10-cloud-run-deployment-config.md`](../../specs/phase-1/10-cloud-run-deployment-config.md)
> GitHub Issue: [#235](https://github.com/Teamie71/folioo-ai/issues/235)

## 의존성

- 1.01 (시각화 워커 서비스 스캐폴드 및 Cloud Tasks Push 핸들러) — FastAPI worker 진입점과 `pptx_worker` package import 구조를 수정하는 후속 보강이다.
- 1.05 (Phase 1 초기 생성 파이프라인 오케스트레이션) — `features.visualization.service` import와 worker handler 위임 경로가 초기 생성 파이프라인을 로드한다.
- 1.06 (시각 QA + Fix-and-Verify 루프 + 프리뷰 업로드) — `features.visualization.qa` import가 isolated worker 테스트 실패 경로에 포함된다.
- 1.07 (Phase 2 재생성/재시도 파이프라인) — regenerate worker path까지 동일한 앱 부팅 smoke 범위에 포함해야 한다.
- 1.10 (Cloud Run 배포 구성) — Dockerfile, dependency group, image smoke 검증을 보강하는 작업이다.

## 사전 준비

- [ ] GitHub Issue #235 본문과 감사 로그의 circular import 재현 조건 확인
- [ ] 전체 테스트 통과와 worker visualization 단독 테스트 실패가 다른 원인인지 재확인
- [ ] 컨테이너 smoke 검증이 실행되는 위치와 배포 파이프라인 연결 지점 확인

## 구현 체크리스트

- [ ] `features.visualization.service` 단독 import 실패 경로를 재현하는 테스트 또는 smoke 체크 추가
- [ ] `pptx_worker.__init__`, 앱 진입점, router import 구조에서 불필요한 eager import 제거
- [ ] `features.visualization.qa` 단독 import가 `pptx_worker.main` 순환 로딩을 유발하지 않도록 의존 방향 정리
- [ ] `pptx_worker.main`, `features.visualization.service`, `features.visualization.qa` import 순서별 smoke 검증 추가
- [ ] `pyproject.toml`의 `pptx-worker` dependency group과 실제 worker runtime import 목록 비교
- [ ] worker runtime에 필요한 LangChain/OpenRouter/dotenv 계열 dependency를 컨테이너 설치 그룹에 반영
- [ ] dev/test dependency에 기대어 컨테이너에서만 실패하는 import가 없는지 확인
- [ ] `apps/pptx-worker/scripts/verify_runtime_image.sh`가 실제 worker app 생성 또는 FastAPI 진입점 import까지 확인하도록 보강
- [ ] Anthropic PPTX toolchain 경로처럼 컨테이너 런타임 필수 환경이 누락되면 빠르게 실패하도록 smoke 체크 추가

## Definition of Done

- [ ] `uv run ruff check .` 통과
- [ ] `uv run pytest` 통과
- [ ] `uv run pytest tests/test_pptx_worker/test_visualization -q` 단독 실행 통과
- [ ] `features.visualization.service`, `features.visualization.qa`, `pptx_worker.main` 단독 import smoke 통과
- [ ] Docker 이미지 smoke 검증에서 실제 worker 앱 부팅 경로와 필수 런타임 의존성 확인

## 리스크 / 메모

- 배포 IAM, Secret, GCS 권한 검증은 1.21에서 다룬다.
- 전체 pytest 통과만으로 완료 처리하지 않는다. 이 task의 핵심은 isolated worker runtime 검증이다.
