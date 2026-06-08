# Cloud Run 배포 구성 (시각화 워커)

## Purpose
`apps/pptx-worker` 를 Cloud Tasks Push 와 정합하는 Cloud Run Service 로 배포하기 위한 컨테이너 이미지와 서비스 사양을 확정해, soffice 메모리 프로파일과 인증 모델에 맞춰 워커를 운영 가능하게 만든다.

## Requirements
- Cloud Run Service 사양을 메모리 4GB(컨테이너 limit 5GB)·2 vCPU·`/tmp` 1GB+·`concurrency=1`·min-instances 0(민감 시 1)·max-instances 20(Cloud Tasks `maxConcurrentDispatches` 와 일치)·요청 timeout 1800s(=`dispatchDeadline` 상한)로 구성하고, `JAVA_TOOL_OPTIONS=-Xmx512m` 로 LibreOffice 내부 JVM 힙을 캡한다.
- Dockerfile 을 ubuntu 22.04 기반으로 libreoffice-impress/core + poppler-utils + fonts-noto-cjk(+extra) + python + defusedxml/lxml/pillow/google-cloud-storage/google-cloud-tasks/fastapi/uvicorn/markitdown 으로 구성한다(§8.3.5).
- 이미지 내부에 PPTX toolchain 호환 스크립트를 번들하고 `ANTHROPIC_PPTX_SKILL_DIR=/app/apps/pptx-worker/toolchain` 로 고정해 런타임 외부 볼륨 없이 `unpack`/`clean`/`pack`/`validate` 가 동작하게 한다.
- Cloud Run 서비스에는 `MAIN_BACKEND_URL`, `MAIN_BACKEND_TIMEOUT`, `OPENROUTER_BASE_URL`, `LLM_MODEL_NAME`, `FILE_PROCESSOR_MODEL_NAME` 과 Secret Manager 기반 `MAIN_BACKEND_API_KEY`, `OPENROUTER_API_KEY` 를 연결한다.
- 서비스를 require-authentication 으로 두고 OIDC 토큰 검증을 Cloud Run IAM 에 위임하며, Cloud Tasks 서비스 계정에 `roles/run.invoker` 를 부여한다.
- 인터뷰 챗 서비스와 빌드/배포 단위를 분리하되 `common/` 패키지는 직접 import 로 재사용한다(ADR-0001).

## Approach
Cloud Run Service 는 HTTP 엔드포인트를 노출해 Cloud Tasks push URL 로 등록되고, 패턴 A(요청 안 동기 처리) 동안 in-flight 요청이 열려 있어 scale-down 으로 죽지 않는다(worker-runtime.md §7.0.1). `concurrency=1` 은 soffice 멀티스레드 안전성 약점을 회피하고 처리량은 인스턴스를 가로로 늘려 확보하며, max-instances 는 LLM rate limit 과 Cloud Tasks 동시성에 맞춘다(worker-spec.md §8.3.8). 이미지 크기는 ~700MB~1GB 로 노드 캐시 hit 덕에 콜드스타트 영향이 작다(§8.3.5). 인증은 인앱 토큰 검증 없이 IAM 위임만 사용한다(spec 01 정합).

## Verification
- 배포된 서비스가 메모리 4GB/limit 5GB·2 vCPU·`/tmp` 1GB+·`concurrency=1`·timeout 1800s·max-instances 20 사양으로 기동하고, `JAVA_TOOL_OPTIONS=-Xmx512m` 가 적용되는지 확인한다.
- 빌드된 이미지에서 soffice·pdftoppm·Noto CJK 폰트·markitdown·필수 Python 패키지와 번들 PPTX toolchain 이 모두 설치되어 있고 `common/` 패키지를 import 할 수 있는지 검증한다.
- Cloud Run YAML 에 필수 env/Secret Manager 참조가 빠짐없이 있으며 Secret 값이 저장소에 노출되지 않는지 확인한다.
- `roles/run.invoker` 를 가진 Cloud Tasks SA 의 OIDC 호출만 통과하고, 권한 없는 호출은 IAM 단계에서 거부되는지 확인한다.
