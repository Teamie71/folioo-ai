# 경험 맵 로컬 데모

메인 서버, OpenRouter API 키, PostgreSQL 없이 경험 맵의 처리 흐름을 확인하는
결정적 데모입니다. 실제 LangGraph 배선, validate, coordinator, SSE 이벤트 모델을
실행하고, LLM·템플릿·커밋 API만 in-memory 대역으로 치환합니다.

```bash
uv run python scripts/experience_map/demo.py
```

출력은 SSE `data:` 형식의 `commit_result`, 결과 메시지, gap 제안 메시지와 마지막
가상 경험 맵입니다. 데모의 커밋은 메모리에서만 반영되며 실제 DB나 메인 서버에는
어떤 요청도 보내지 않습니다.

이 데모는 UI·실제 메인 서버 계약 검증을 대체하지 않습니다. 실서비스 준비에는
`tasks/phase-3-experience-map/23-scenario-tests-operational-readiness.md`의 연동 검증이
추가로 필요합니다.
