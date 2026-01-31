# 언어 설정
- 모든 응답은 한국어로 작성하세요.
- 코드 주석과 문서는 한국어로 작성하세요.
- 커밋 메시지는 영어로 작성하세요.

# 디렉토리 구조
```
.
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   └── create-feature.md
│   └── PULL_REQUEST_TEMPLATE.md
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   └── init.py
│   │   └── init.py
│   ├── db/
│   │   ├── migrations/
│   │   │   └── init.py
│   │   └── init.py
│   ├── models/
│   │   └── init.py
│   ├── schemas/
│   │   └── init.py
│   └── init.py
├── common/
│   ├── llm/
│   │   ├── init.py
│   │   └── client.py
│   ├── utils/
│   │   └── init.py
│   ├── vector_store/
│   │   └── init.py
│   └── init.py
├── features/
│   ├── correction/
│   │   └── init.py
│   ├── interview/
│   │   ├── agents/
│   │   │   ├── nodes/
│   │   │   │   ├── init.py
│   │   │   │   ├── analyst.py
│   │   │   │   ├── file_processor.py
│   │   │   │   ├── question_generator.py
│   │   │   │   ├── retriever.py
│   │   │   │   └── router.py
│   │   │   ├── prompts/
│   │   │   │   ├── init.py
│   │   │   │   ├── analyst.py
│   │   │   │   └── question_generator.py
│   │   │   ├── init.py
│   │   │   ├── graph.py
│   │   │   └── state.py
│   │   ├── config/
│   │   │   ├── init.py
│   │   │   ├── loader.py
│   │   │   └── stages.yaml
│   │   └── init.py
│   └── init.py
├── tests/
│   └── test_features/
│       └── test_interview/
│           ├── test_config.py
│           └── test_graph.py
├── .gitignore
├── .pre-commit-config.yaml
├── .python-version
├── README.md
├── langgraph.json
├── main.py
├── pyproject.toml
└── uv.lock
```

# 작업 범위 및 제약사항
- **중요**: 명시적으로 요청한 변경사항만 수행하세요. 추가 작업을 하지 마세요.
- 특별히 지시하지 않는 한 코드를 "개선"하거나 "정리"하려 하지 마세요.
- 특정 변경을 요청하면 **오직 그 변경만** 수행하세요 - "개선"이나 "최적화"를 추가하지 마세요.
- 명시적으로 요청하지 않는 한 리팩토링하지 마세요.
- 오류 수정이나 리팩토링을 제안할 수 있지만, 명확한 승인을 받기 전에는 실행하지 **마세요**.
- 모든 추가 작업, 개선, 정리, 최적화, 오류 수정, 리팩토링 작업의 경우에는 제안을 하세요.

# 추가 컨텍스트
- Always respond in Korean
- Use Korean for all explanations, comments, and documentation
