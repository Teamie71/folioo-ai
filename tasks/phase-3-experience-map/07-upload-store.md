---
id: "3.07"
phase: 3
title: "임시 첨부 파일 저장과 수명 관리"
spec: "docs/architecture/experience-map-agent.md"
depends_on: ["3.02"]
blocks: ["3.10", "3.12"]
estimate: "M"
status: "todo"
owner: ""
sprint: ""
---

# Task 3.07 — 임시 첨부 파일 저장과 수명 관리

> Spec: [`docs/architecture/experience-map-agent.md`](../../docs/architecture/experience-map-agent.md) 9절 7번
> PR: EM-07 · 브랜치 `feat/{issue}-experience-map-upload-store`
> GitHub Issue: [#305](https://github.com/Teamie71/folioo-ai/issues/305)

## 의존성

- 3.02 (스키마·오류 모델) — 업로드 검증 오류 타입을 사용한다.

## 사전 준비

- [x] 기존 GCS 클라이언트(`features/visualization` 계열) 재사용 가능 여부 확인
- [x] request 전용 임시 object 경로 규칙 결정
- [x] 로깅 정책 확인 (파일명·본문이 로그에 남지 않아야 함)

## 구현 체크리스트

- [x] `upload_store.py` — TXT·DOCX·PPTX·PDF·PNG·JPEG 의 MIME·확장자·file signature 검사
- [x] `.txt` 는 UTF-8 디코딩으로 검증
- [x] 요청당 최대 3개, 파일당 최대 10MB 제한
- [x] 업로드 스트리밍 중 SHA-256 계산
- [x] GCS request 전용 임시 object 업로드
- [x] request claim 실패 또는 저장 결과 재전송이면 방금 올린 object 즉시 삭제
- [x] 추출 성공 시 즉시 삭제, 추출 실패 object 는 1시간 TTL
- [x] 만료 object 정리 job 또는 bucket lifecycle 설정

## Definition of Done

- [x] 다른 worker 에서 추출 재시도가 가능하다
- [x] 추출 실패 후 1시간 안에는 원본으로 재시도된다
- [x] **파일명·본문·추출 원문이 로그에 남지 않는다**
- [x] 확장자만 위조한 파일이 file signature 검사에서 거부된다
- [x] 크기·개수 초과가 업로드 완료 전에 거부된다
- [x] `ruff check .` · `ruff format --check .` · `pytest` 통과

## 리스크 / 메모

- 개인정보가 담긴 이력서·포트폴리오가 올라온다. 삭제 시점과 로깅 정책이 이 태스크의 핵심이다.
- 결과: `1095 passed` (신규 37). ruff check·format 통과.
- pptx-worker 의 `GcsClient` 는 job/template 키 기반 동기 클라이언트라 재사용하지 않았다. `ObjectStore` Protocol 로 GCS 를 감싸 테스트에서 대체한다.
- **UTF-8 증분 디코더를 쓴다.** 고정 길이 probe 로 자르면 3바이트인 한글이 문자 중간에서 끊겨 정상 한글 txt 가 전부 거부된다. 테스트가 이 버그를 잡았다.
- 만료 정리는 `sweep_expired()` 로 구현했다. bucket lifecycle 규칙으로 대체 가능하며 둘 중 하나만 있으면 된다.
