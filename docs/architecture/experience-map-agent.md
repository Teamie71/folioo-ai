# 경험정리 에이전트 개발 통합 문서

> **범위**: AI 서버 구현. 계약(API·DB)은 [경험정리 API 명세](experience-map-api-spec.md)를 따릅니다.
>
> 원본 기획: 노션 「에이전트」, 「경험정리 템플릿」
> 흐름: [FigJam 에이전트 보드](https://www.figma.com/board/v0wh7Srv8Xd8DwNSnIHdUy/%EC%97%90%EC%9D%B4%EC%A0%84%ED%8A%B8?node-id=0-1)
> 계약 결정 경위: [계약 변경 제안 결정 사항](experience-map-contract-decisions.md)
> 실행 계획: [PR 분할 계획](experience-map-pr-plan.md) — 9절 개발 순서를 PR 단위로 분해

---

## 1. 개요

### 1-1. 에이전트 정의

취준생이 자신의 경험을 정리하는 과정을 보조하는 에이전트입니다. 경험을 떠올리고,
정리하고, 취업 준비에 활용하는 과정의 부담을 줄이는 것을 지향합니다.

**v.3 범위는 "사용자가 제공한 정보를 정해진 블록 구조에 맞게 정리하는 것"으로
한정합니다.** 첨삭·코칭·자유 상담은 범위 밖이며 Fallback으로 처리합니다.

### 1-2. 기능 4가지

| # | 기능 | 경로 |
| --- | --- | --- |
| 1 | 파일 업로드 시 블록으로 정리 | Router → 파일처리 → 필터링 → 구조화 |
| 2 | 채팅 입력 시 블록으로 정리 | Router → 필터링 → 구조화 |
| 3 | 제안된 gap을 해소하여 블록에 반영 | Router → 필터링 → 구조화 **또는** 문장 정제 |
| 4 | Fallback | Router → Fallback |

2번은 **기존 블록에 내용을 덧붙이지 않고 새 블록으로** 정리합니다.
기존 블록 수정은 3번(gap 답변)에서만 일어납니다.

### 1-3. 공통 규칙

- **제공되지 않은 정보를 생성하지 않습니다.** 모든 노드에 적용되는 최상위 규칙입니다
- 실패 시 노드마다 1회 자동 재시도
- gap 분석·제안 생성은 실패해도 재시도하지 않고 화면에 표시하지 않음
- 사용자가 재시도 버튼을 누르면 실패한 노드부터 재실행

---

## 2. 경험정리 체계

### 2-1. 좋은 경험정리의 특징

문장 정제와 gap 분석의 판단 기준입니다. 프롬프트에 그대로 반영합니다.

**① 구체적인 기록**

추상적 표현이 아니라 실제 행동과 맥락, 사고 과정이 드러나야 합니다.
표면적 행동·결정(What), 구체적 수행 방식(How), 행동한 이유(Why)가 모두 포함될 것.

- 어떤 방식으로 조사했는가
- 어떤 기준으로 판단했는가
- 그 문제를 해결해야 했던 이유는 무엇인가
- 어떤 결과가 나타났는가

**② 수치 포함**

- 증가율·달성률·전환율·참여자 수 등 정량 성과
- 목표 대비 실제 결과의 차이
- 실행 전후의 변화 폭
- 기간·횟수 등 행동의 진정성이 드러나는 증거

**③ 배운 점과 활용 방향 포함**

- 아쉬웠던 점과 개선 방향
- 이후 유사한 상황에서 적용할 방식
- 이전보다 나아진 역량이나 관점
- 얻은 인사이트

### 2-2. 블록 위계

블록은 경험정리를 구성하는 최소 정보 단위입니다. 경험은 하나의 긴 글이 아니라
목적이 구분된 여러 블록으로 저장됩니다.

| level | 이름 | 정의 |
| --- | --- | --- |
| 1 | 그룹 | 여러 활동을 시기·유형·주제로 묶는 최상위 단위 |
| 2 | 활동 | 사용자가 실제로 수행한 하나의 활동. 공통된 맥락과 목표를 공유 |
| 3 | 카테고리 | 하나의 활동을 목적에 따라 구분하는 단위 |
| 4 | 항목 | 카테고리를 구성하는 하위 단위 |
| 5 | 세부 항목 | 항목을 더 구체적으로 나누는 단위 |

### 2-3. AI 권한

| level | 생성 | 수정 | 삭제 |
| --- | --- | --- | --- |
| 1 그룹 | X | X | X |
| 2 활동 | X | X | X |
| 3 카테고리 | **O** | X | X |
| 4 항목 | O | O | X |
| 5 세부 항목 | O | O | X |

3단계 카테고리를 생성하는 두 경우:

- 기본 제공 카테고리 중 해당 활동에 **없는 것이 필요할 때** → 해당 `section_kind`로 생성
- 사용자 입력이 기본 카테고리 **어디에도 맞지 않을 때** → level 3 `CONTENT`로 생성

**AI는 어떤 위계의 블록도 삭제하지 않습니다.** 최종 검증은 메인 서버가 커밋 API에서
수행하지만, AI도 `validate` 노드에서 사전 차단합니다.

### 2-4. 기본 제공 카테고리 5종

| 카테고리 | 목적 | 4단계에 담는 내용 | 5단계에 담는 내용 |
| --- | --- | --- | --- |
| 상세정보 | 활동의 기본 맥락과 배경 | 배경·목표, 진행 기간, 역할과 분담, 타깃, 활용 기술·툴 | 4단계의 구체적 하위 내용이 있는 경우 |
| 주요성과 | 활동으로 만들어낸 결과 | 정량 성과, 정성 성과 | 4단계의 구체적 하위 내용이 있는 경우 |
| 담당업무 | 직접 맡은 업무와 실행 방식 | 담당한 주요 업무 또는 역할 | **템플릿으로 에피소드 구체화** |
| 문제해결 | 발생한 문제와 해결 과정 | 에피소드 한 줄 요약 | **템플릿으로 에피소드 구체화** |
| 배운 점 | 활동으로 얻은 학습과 성장 | 배우거나 성장한 점, 활용 계획 | 4단계의 구체적 하위 내용이 있는 경우 |

---

## 3. 경험정리 템플릿

블록을 생성할 때 `slot_id`로 템플릿 슬롯을 지정하면 **메인 서버가 카탈로그에서
placeholder 문구를 부여**합니다. AI는 문구를 재생산하지 않고 식별자만 고릅니다.

카탈로그는 `GET /templates`로 받아 기동 시 1회 조회 후 1시간 TTL로 갱신합니다.

**기본 placeholder**: 아래에 명시되지 않은 블록은 "내용을 입력해 주세요."

### 3-0. `slot_id` 카탈로그 구조

전체 **38개**이며 level에 따라 형식이 다릅니다.

```text
level 4 : {SECTION}.{SLOT}              DETAIL.MOTIVATION
level 5 : {SECTION}.{TEMPLATE}.{SLOT}   PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE
```

**점 개수가 곧 level입니다.** 2-part면 4단계, 3-part면 5단계입니다.

```text
카테고리 슬롯 (level 4)   10개   ← 카테고리 생성 시 함께 전개 (3-2)
하위 템플릿   (level 5)   28개   ← 담당업무 1종×4 + 문제해결 6종×4 (3-3·3-4)
                          38개
```

**하위 템플릿은 담당업무·문제해결만 가집니다.** 상세정보·주요성과·배운 점은
4단계에서 끝납니다.

#### 앵커 구조

level 5 블록은 반드시 **앵커 슬롯으로 만든 level 4 블록 아래**에 붙습니다.

| `section_kind` | 앵커 슬롯 | 하위 템플릿 |
| --- | --- | --- |
| `TASK` | `TASK.SUMMARY` | 담당업무 1종 (3-3) |
| `PROBLEM_SOLVING` | `PROBLEM_SOLVING.SUMMARY` | 6종 중 택1 (3-4) |

items에서 같은 요청에 앵커를 만들었으면 `parent_item_id`로, 기존 블록이면
`parent_id`로 참조합니다.

#### 두 가지 사용 경로

| 경우 | items 구성 |
| --- | --- |
| 새 업무·에피소드를 만드는 경우 | 앵커 level 4 **+ 하위 템플릿 level 5 전체** |
| 기존 4단계 아래를 보강하는 경우 | 하위 템플릿 level 5만 |

#### 반복 가능

담당업무는 **업무 하나당 한 벌**, 문제해결은 **에피소드 하나당 한 벌**입니다.
한 활동에 여러 벌이 들어갈 수 있습니다.

### 3-1. 빈 블록 생성 규칙

| 경우 | 규칙 |
| --- | --- |
| 템플릿을 쓰지 않는 생성 | 값이 들어갈 블록만 생성 |
| **템플릿을 쓰는 생성** | **모든 슬롯을 생성.** 채울 수 있는 블록만 채우고, 정보가 없는 블록은 `content` 없이 `slot_id`만 보냄 |

빈 슬롯도 AI가 items에 포함해 보냅니다. 메인이 템플릿을 전개해 주지 않습니다.

'기술 트러블슈팅'을 적용했는데 사용자가 5단계 4개 중 2개 분량만 말했다면
**4개를 모두 만들고 2개만 채웁니다.** 나머지는 화면에 placeholder가 보이고,
gap 분석이 그 블록을 근거로 후속 질문을 만듭니다.

### 3-2. 3단계 템플릿

카테고리를 새로 생성할 때 함께 만드는 하위 슬롯입니다.
**표의 슬롯을 모두 생성**하며, 정보가 없어 채울 수 없는 슬롯은 `content` 없이
`slot_id`만 보냅니다 (3-1). 순서는 표를 따릅니다.

| 카테고리 | level | `slot_id` | placeholder | 작성 예시 |
| --- | --- | --- | --- | --- |
| 상세정보 | 4 | `DETAIL.MOTIVATION` | 어떤 계기로 이 경험을 시작했으며, 최종적으로 달성하고자 한 목표는 무엇인가요? | 교내 커뮤니티의 비효율적인 게시판형 거래 방식을 개선하고, 전공 서적 거래의 편의성과 신뢰도를 높이기 위한 전용 플랫폼 기획 및 앱 리뉴얼 |
| | 4 | `DETAIL.PERIOD` | 전체 진행 기간은 언제부터 언제까지였나요? | 2023.09 ~ 2023.12 (4개월) |
| | 4 | `DETAIL.ROLE` | 본인의 역할은 무엇이었으며, 전체 인원과 역할 분담은 어떻게 구성되었나요? | 기획 1명 (본인), 디자인 1명, 개발 2명 (총 4인 팀) |
| | 4 | `DETAIL.TARGET` | 주요 타깃, 사용자, 혹은 고객은 누구였나요? | 비싼 전공 서적 가격에 부담을 느끼며, 교내 직거래를 통해 택배비 절약과 빠른 거래를 원하는 대학생 |
| | 4 | `DETAIL.STACK` | 진행 과정에서 본인이 직접 활용한 기술, 방법론, 혹은 툴은 무엇인가요? | Figma, Notion, Slack, Google Analytics, IDI(심층 인터뷰), Usability Test |
| 주요성과 | 4 | `ACHIEVEMENT.QUANTITATIVE` | 수치로 증명할 수 있는 정량적인 성과는 무엇인가요? | 리뉴얼 전 대비 DAU(일간 활성 사용자) 150% 증가 |
| | 4 | `ACHIEVEMENT.QUALITATIVE` | 간접적인 지표로 확인할 수 있는 정성적인 성과는 무엇인가요? | "검색부터 구매 약속까지 과정이 직관적이다"라는 사용자 피드백 다수 확보 |
| 담당업무 | 4 | **`TASK.SUMMARY`** (앵커) | 담당한 주요 업무 또는 역할을 적어주세요. | 사용자 리서치 및 문제 정의 |
| | 5 | — | 담당업무 템플릿 3-3을 따름 | |
| 문제해결 | 4 | **`PROBLEM_SOLVING.SUMMARY`** (앵커) | 문제해결 에피소드를 한 줄로 요약해 주세요. | 신규 프로모션 페이지 가입 이탈 문제 해결 |
| | 5 | — | 문제해결 템플릿 3-4를 따름 | |
| 배운 점 | 4 | `LEARNING.GROWTH` | 이 경험을 통해 새롭게 배우거나 성장한 점은 무엇이며, 향후 어떻게 활용할 계획인가요? | 이번 프로젝트에서는 구글 애널리틱스를 기초적으로만 활용했지만, 향후에는 SQL을 학습하여 직접 DB에서 데이터를 추출하고 더 정교하게 사용자 행동 데이터를 분석해 보고 싶다. |

level 4 슬롯 **10개**입니다. 앵커 둘만 하위 템플릿을 가집니다 (3-0).

### 3-3. 담당업무 템플릿

사용 조건 두 가지:

- **4단계 블록을 새로 생성하는 경우** — 4단계 placeholder는
  "담당한 주요 업무 또는 역할을 적어주세요."
- **이미 있는 4단계 블록 아래에 새 5단계를 생성하는 경우**

표의 5단계 블록은 모두 해당 4단계 아래에 생성하며, 순서는 표를 따릅니다.
템플릿은 **기본 1종**입니다.

**`template_id`: `BASIC`** — 전체 ID 는 `TASK.BASIC.{SLOT}`

| level | `slot_id` | placeholder | 작성 예시 |
| --- | --- | --- | --- |
| 5 | `TASK.BASIC.PURPOSE` | 이 업무를 진행한 목적은 무엇이며, 구체적으로 어떤 목표를 달성하고자 했나요? | 신규 브랜드 인지도를 확대하고, 2030 타겟 고객의 공식 SNS 채널 팔로워 1만 명 확보를 목표로 설정 |
| 5 | `TASK.BASIC.RESEARCH` | 원활한 업무 수행을 위해 조사한 정보나 추가로 학습한 내용은 무엇인가요? | 최근 소셜 미디어 알고리즘 변화와 타겟층이 선호하는 숏폼 영상 트렌드, 타사의 바이럴 성공 사례를 집중적으로 조사 |
| 5 | `TASK.BASIC.EXECUTION` | 실제 작업은 어떤 방식으로, 어떤 과정을 거쳐서 진행했나요? | 브랜드 핵심 메시지를 15초 이내로 압축한 숏폼 시리즈를 제작하고, A/B 테스트를 통해 반응률이 높은 소재에 광고 예산을 집중하는 방식으로 운영 |
| 5 | `TASK.BASIC.RESULT` | 업무 완료 후 나타난 결과는 무엇이며, 이 과정을 통해 배운 점은 무엇인가요? | 캠페인 한 달 만에 목표 팔로워 1만 명을 조기 달성했으며, 영상 도입부 3초의 시각적 요소가 사용자 체류 시간과 전환에 미치는 결정적인 영향을 체득 |

### 3-4. 문제해결 템플릿 (6종)

**4단계 블록은 공통으로 생성**하며 placeholder는
"문제해결 에피소드를 한 줄로 요약해 주세요."입니다.
5단계만 아래 6종 중 내용에 맞는 것을 **AI가 선택**합니다.

#### 기본

**`template_id`: `BASIC`** — 전체 ID 는 `PROBLEM_SOLVING.BASIC.{SLOT}`

| `slot_id` | placeholder | 작성 예시 |
| --- | --- | --- |
| `PROBLEM_SOLVING.BASIC.PROBLEM`  어떤 문제가 발생했으며, 이를 해결해야 했던 이유는 무엇인가요? | 신규 프로모션 페이지 이탈률 70% 초과, 목표 가입자 수 달성을 위해 전환율 개선 필요 |
| `PROBLEM_SOLVING.BASIC.CAUSE`  문제의 원인은 무엇이었고, 어떤 방식으로 원인을 파악했나요? | GA4 퍼널 분석으로 사용자 이탈 구간을 추적하여, 모호한 CTA 카피와 복잡한 혜택 설명이 가입 단계의 병목 원인임을 확인 |
| `PROBLEM_SOLVING.BASIC.SOLUTION`  해결책을 도출한 과정과 구체적인 실행 방법은 무엇인가요? | 핵심 혜택을 직관적으로 강조한 3가지 카피로 A/B 테스트 기획, 일정 기간 노출하여 클릭률 변화를 추적 |
| `PROBLEM_SOLVING.BASIC.RESULT`  해결책 적용 후 나타난 결과와 그 검증 방법, 그리고 이 과정을 통해 배운 점은 무엇인가요? | 개선안 적용 후 가입 전환율 15% 상승, 타깃 니즈에 맞춘 직관적인 메시징과 데이터 기반 가설 검증의 중요성을 체득 |

#### 대인관계

**`template_id`: `INTERPERSONAL`** — 전체 ID 는 `PROBLEM_SOLVING.INTERPERSONAL.{SLOT}`

| `slot_id` | placeholder | 작성 예시 |
| --- | --- | --- |
| `PROBLEM_SOLVING.INTERPERSONAL.SITUATION`  누구와 어떤 상황에서 의견 차이나 문제가 발생했나요? | 자료 조사 범위와 회의 진행 방식을 두고 팀원들 간의 의견 대립 및 참여도 저하 발생 |
| `PROBLEM_SOLVING.INTERPERSONAL.ACTION`  문제를 해결하기 위해 상대방과 어떻게 소통하고 어떤 행동을 취했나요? | 팀원들과 개별 면담을 통해 각자의 불만 사항과 상황을 청취. 이후 회의 시간 제한, 역할 재분배 등 모두가 동의할 수 있는 명확한 규칙 수립 및 제안 |
| `PROBLEM_SOLVING.INTERPERSONAL.OUTCOME`  본인의 대응으로 인해 상대방의 반응이나 상황은 어떻게 변화하고 마무리되었나요? | 새로운 규칙 도입 후 팀원들이 불만을 해소하고 적극적으로 아이디어를 제시하기 시작했으며, 갈등 없이 기한 내에 최종 기획서 제출 완료 |
| `PROBLEM_SOLVING.INTERPERSONAL.LEARNING`  이 과정을 통해 배운 점은 무엇이며, 향후 유사한 상황에 어떻게 적용할 계획인가요? | 상호 존중을 바탕으로 한 개별 소통과 명확한 규칙 수립이 팀워크에 미치는 긍정적인 영향을 배움. 향후 협업 시 초기 단계부터 명확한 역할 분담과 규칙을 세팅할 계획 |

#### 성과 부진 개선

**`template_id`: `PERFORMANCE`** — 전체 ID 는 `PROBLEM_SOLVING.PERFORMANCE.{SLOT}`

| `slot_id` | placeholder | 작성 예시 |
| --- | --- | --- |
| `PROBLEM_SOLVING.PERFORMANCE.METRIC`  문제가 된 성과 지표는 무엇이며, 목표치와 실제 상태의 차이는 어느 정도였나요? | 뉴스레터 오픈율 목표치는 25%이지만, 12%에 머물러 있어 개선이 시급한 상황 |
| `PROBLEM_SOLVING.PERFORMANCE.CAUSE`  목표에 도달하지 못한 근본적인 원인을 무엇으로 분석했나요? | 기존 구독자 데이터 분석 결과, 발송 시간대가 타깃의 주 활동 시간과 맞지 않고 제목이 길어 클릭을 유도하지 못함을 확인 |
| `PROBLEM_SOLVING.PERFORMANCE.ACTION`  개선을 위해 기존 방식을 어떻게 변경하고 어떤 새로운 시도를 했나요? | 발송 시간을 출근 시간대로 변경하고, 제목을 15자 이내로 단축하여 핵심 키워드를 전면에 배치 |
| `PROBLEM_SOLVING.PERFORMANCE.RESULT`  실행 후 지표는 어떻게 달라졌으며, 개선 효과를 어떻게 검증했나요? | 변경 후 오픈율 28%로 상승. A/B 테스트를 통해 제목 길이와 발송 시간의 상관관계를 교차 검증하여 효과 입증 |

#### 기술 트러블슈팅

**`template_id`: `TROUBLESHOOTING`** — 전체 ID 는 `PROBLEM_SOLVING.TROUBLESHOOTING.{SLOT}`

| `slot_id` | placeholder | 작성 예시 |
| --- | --- | --- |
| `PROBLEM_SOLVING.TROUBLESHOOTING.PROBLEM`  어떤 문제가 발생했으며, 그 문제가 미친 구체적인 영향 범위는 어디까지였나요? | 대규모 트래픽 발생 시 결제 페이지 로딩 속도가 5초 이상 지연되어 사용자의 결제 이탈 발생 |
| `PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE`  문제의 원인은 무엇이었으며, 이를 파악하기 위해 어떤 검증 과정을 거쳤나요? | APM 툴을 활용해 병목 구간을 모니터링한 결과, 불필요한 데이터베이스 쿼리의 중복 호출이 원인임을 확인 |
| `PROBLEM_SOLVING.TROUBLESHOOTING.SOLUTION`  어떤 해결책을 선택하여 적용했으며, 여러 방법 중 그 방법을 채택한 이유는 무엇인가요? | 쿼리 최적화 및 캐싱(Redis) 도입 선택. 서버 증설보다 비용 효율적이고 근본적인 성능 개선이 가능하기 때문 |
| `PROBLEM_SOLVING.TROUBLESHOOTING.VERIFICATION`  해결 여부를 어떻게 검증했으며, 재발 방지를 위해 어떤 대책을 수립했나요? | 부하 테스트 도구로 시뮬레이션하여 응답 속도가 1초 이내로 단축됨을 확인. 이후 슬로우 쿼리 알림 모니터링 시스템 구축 |

#### 피드백 대응

**`template_id`: `FEEDBACK`** — 전체 ID 는 `PROBLEM_SOLVING.FEEDBACK.{SLOT}`

| `slot_id` | placeholder | 작성 예시 |
| --- | --- | --- |
| `PROBLEM_SOLVING.FEEDBACK.RECEIVED`  어떤 요청이나 불편 사항, 피드백이 반복적으로 접수되었나요? | 사내 비품 신청 과정이 전반적으로 어렵다는 불만이 다수 접수됨 |
| `PROBLEM_SOLVING.FEEDBACK.NEED`  표면적인 의견 뒤에 있는 실제 니즈나 근본적인 문제점은 무엇으로 파악했나요? | 신청 양식 간소화뿐만 아니라, 신청 내역과 투명한 진행 상황 공유가 사용자들의 핵심 니즈임을 파악 |
| `PROBLEM_SOLVING.FEEDBACK.ACTION`  이를 해결하기 위해 구체적으로 어떤 대응책이나 개선안을 실행했나요? | Notion을 활용하여 신청 양식을 통일하고, 칸반 보드 형태로 처리 상태를 실시간으로 확인 가능하게 개선 |
| `PROBLEM_SOLVING.FEEDBACK.OUTCOME`  조치 이후 피드백을 준 대상의 반응이나 상황은 어떻게 달라졌나요? | 비품 신청 관련 중복 문의가 80% 감소했으며, 팀원들로부터 업무 효율성과 투명성이 크게 높아졌다는 긍정적 피드백 확보 |

#### 실패 회복

**`template_id`: `RECOVERY`** — 전체 ID 는 `PROBLEM_SOLVING.RECOVERY.{SLOT}`

| `slot_id` | placeholder | 작성 예시 |
| --- | --- | --- |
| `PROBLEM_SOLVING.RECOVERY.FAILURE`  아쉬웠던 결과, 구체적인 실수, 혹은 직면했던 한계는 무엇이었나요? | 첫 프로젝트 진행 시, 아이디어 기획에 과도한 시간을 쏟아 핵심 기능 구현을 기한 내에 마치지 못함 |
| `PROBLEM_SOLVING.RECOVERY.CAUSE`  이러한 결과나 실수가 발생하게 된 핵심적인 원인은 무엇이라고 판단했나요? | 완벽한 결과물을 만들고자 하는 욕심으로 인해, MVP 정의와 작업의 우선순위 설정에 실패한 것이 원인 |
| `PROBLEM_SOLVING.RECOVERY.EFFORT`  이를 극복하고 보완하기 위해 구체적으로 어떤 노력을 했나요? | 애자일 방법론과 스프린트 개념을 학습하고, 다음 프로젝트부터는 핵심 기능 위주로 백로그를 작성하여 일정 관리 방식을 개선 |
| `PROBLEM_SOLVING.RECOVERY.CHANGE`  이전과 비교하여 결과가 어떻게 변화했나요? | 두 번째 프로젝트에서는 주어진 기한 내에 성공적으로 프로토타입을 배포하고 사용자 테스트까지 완료하며 목표 달성 |

### 3-5. 기본 제공 데이터 (참고)

신규 사용자 진입 시 **메인 서버가 생성**합니다. AI 서버는 읽기만 합니다.

```text
미분류 (그룹)
새로운 그룹 1 (그룹)
└ 새로운 경험 1 (활동)
  ├ 상세정보   — 4단계 5개 (3-2와 동일)
  ├ 주요성과   — 4단계 2개
  ├ 담당업무   — 4단계 1개 + 5단계 4개
  ├ 문제해결   — 4단계 1개 + 5단계 4개
  └ 배운 점    — 4단계 1개
```

---

## 4. 파이프라인

```text
사용자 입력
  → Router ─┬→ 파일처리 ─→ 반영 내용 필터링
            ├→ 반영 내용 필터링
            └→ Fallback → 결과 응답

  반영 내용 필터링 ─┬→ 블록 단위 구조화 ─→ 문장 정제
                    └→ 문장 정제

  문장 정제 → validate ─┬→ 커밋 → 결과 응답
                        └→ gap 분석 → 제안 응답

  validate 회귀: 위계·권한 위반 → 구조화 / 글자수 위반 → 문장 정제 (최대 2회)
```

커밋과 gap 분석은 병렬입니다. LangGraph의 fan-out은 superstep이 모두 끝나야
다음으로 넘어가므로 **graph 안에서 병렬로 연결하지 않고 service coordinator가
처리**합니다 (9절 17번).

---

## 5. 노드 명세

### 5-1. Router

| 항목 | 내용 |
| --- | --- |
| Goal | 사용자 입력 의도를 파악해 적절한 노드로 분기 |
| Input | 사용자 입력, 파일 업로드 여부 |
| Output | `intent` |

| intent | 다음 노드 | 조건 |
| --- | --- | --- |
| `file_input` | `file_processor` | 파일이 업로드된 경우 (**코드로 판정**) |
| `chat_input` | `content_filter` | 채팅으로 반영 가능한 내용을 입력했거나 반영을 요청 |
| `out_of_scope` | `fallback` | 기능 범위 밖 |

**`out_of_scope` 판정은 보수적으로** 합니다. 블록에 반영될 여지가 조금이라도 있으면
`content_filter`로 보냅니다. 명확한 fallback 대상:

- 경험정리·취업 준비와 무관한 입력
- 자기소개서·포트폴리오·면접 질문 생성 등 실제 지원 과정 보조 요청
- 정리된 경험에 대한 질문 또는 품질 판단 요청
- 내용 없이 블록 생성·수정만 요청
- 블록 이동 또는 삭제 요청

**gap 답변 여부는 Router가 판정하지 않습니다.** 활성 gap이 있어도 일단
`content_filter`로 보냅니다. LLM 분류가 재시도 후에도 실패하면 fallback입니다.

### 5-2. 파일처리

| 항목 | 내용 |
| --- | --- |
| Goal | 업로드한 파일의 텍스트 추출 |
| Input | 업로드한 파일 |
| Output | 후속 노드, 추출 텍스트 |

| 처리 | 형식 |
| --- | --- |
| 파일 파서 | TXT, DOCX, PPTX |
| OCR 모델 | PDF, PNG, JPG/JPEG |

- 한 요청에 두 종류가 섞이면 각각 처리 후 **입력 순서대로** 이어 붙입니다
- 파일별 추출 결과와 source hash를 저장하고, 파일별·전체 context 길이를 제한합니다
- 추출 결과를 checkpoint에 저장한 뒤 GCS 원본을 즉시 삭제합니다

**후속 처리 — 두 실패를 구분해야 합니다.**

| 상황 | 처리 |
| --- | --- |
| 추출 성공 | `content_filter` |
| 파일 상태·품질 문제로 추출 불가 | **`fallback`** (노드 실패 아님) |
| 시스템 오류 (타임아웃·API 실패) | 노드 실패 → 자동 재시도 |

손상된 PDF를 올린 사용자에게 재시도 버튼을 보여주면 몇 번을 눌러도 같은 결과입니다.

### 5-3. 반영 내용 필터링

| 항목 | 내용 |
| --- | --- |
| Goal | 입력 전체를 세 가지로 분류하고 후속 노드를 결정 |
| Input | 사용자 입력, 파일 추출 텍스트, `active_gap`(있을 때) |
| Output | 후속 노드, gap 답변 텍스트, 새로 반영할 텍스트 |

**분류 기준**

| 분류 | 기준 |
| --- | --- |
| 활성 gap 답변 | `active_gap`이 있고 그 질문에 답하거나 지적한 정보를 제공한 내용 |
| 새로 반영할 내용 | 사용자가 수행한 경험의 맥락·과정·결과·학습. **사용자가 반영을 요청한 내용은 반드시 여기** |
| 반영 제외 | 겪지 않은 경험, 일반 지식 나열, 무관한 내용, 사용자가 제외를 요청한 내용, 요구사항 텍스트("이 문서 정리해줘") |

**Tool — 조건부 호출**

경험정리 내용 조회 (해당 활동의 2~5단계 전체 블록). 사용자 요청 수행에 기존
내용과의 비교가 필요할 때만 사용합니다.

- "이미 있는 내용은 제외하고 추가해"
- "현재 활동에 해당하는 내용만 골라서 추가해"

**후속 노드 분기**

| 분류 결과 | 다음 |
| --- | --- |
| gap 답변만 | `active_gap.gap_type`이 정한 노드 |
| 새 내용만 | 구조화 |
| 구조화가 필요한 gap 답변 + 새 내용 | 둘 다 구조화 |
| 정제가 필요한 gap 답변 + 새 내용 | 새 내용은 구조화 → 구조화 결과와 gap 답변을 함께 정제 |
| 반영 제외만 | fallback |

반영 제외 내용은 폐기합니다. 입력에 없는 역할·성과·수치를 생성하지 않습니다.

### 5-4. 대상 활동 선택

- `context_experience_id`가 유효하면 우선 사용
- 없으면 사용자 메시지와 outline으로 선택
- gap 답변은 `anchor_block_id`가 속한 활동을 사용
- **하나로 특정할 수 없으면 commit 없이 fallback**

**한 요청은 한 활동만 수정합니다.**

### 5-5. 블록 단위 구조화

| 항목 | 내용 |
| --- | --- |
| Goal | **텍스트를 수정하지 않고** 위계에 맞게 분류 |
| Input | 새로 반영할 텍스트, gap 답변 텍스트, `anchor_block_id` |
| Output | 구조화 결과 (items) |

**구조화 기준**

- 새 내용: 적절한 3단계 카테고리 판단 → 4단계 항목 → 필요 시 5단계 세부 항목
- gap 답변(`new_child_block`): `anchor_block_id` 하위 블록으로 구조화
- 필요한 카테고리가 활동에 없으면 **카테고리를 생성** (2-3)
- 부모가 **기존 블록**이면 `parent_ref`(별칭), **같은 요청에서 새로 만든 블록**이면
  `parent_item_id`(앞선 item_id). **둘을 동시에 채우지 않습니다** — 커밋 API의
  `parent_id`/`parent_item_id`와 1:1로 대응하며, 둘 다 오면 validate에서 탈락

**처리 기준**

- 입력 내용의 **구체성을 반드시 유지**
- 전달된 텍스트를 수정하지 않고 블록 단위로 분류만
- 기존 블록을 수정하지 않음 (gap `extend_block`은 이 노드를 거치지 않음)
- 한 요청에서 같은 target 중복 update 금지
- LLM 출력에 `level`·`position`은 받지 않음 (메인 서버가 계산)

**템플릿 선택**

| 생성 대상 | 템플릿 |
| --- | --- |
| 3단계 카테고리 | 3단계 템플릿 (3-2) |
| 담당업무 하위 4·5단계 | 담당업무 템플릿 기본 1종 (3-3) |
| 문제해결 하위 5단계 | **6종 중 선택** (3-4) |
| 그 외 | 기본 placeholder |

문구가 아니라 `slot_id`를 고릅니다. 프롬프트에는 카탈로그의 **작성 예시**를 함께
넣어 few-shot으로 사용합니다.

### 5-6. 문장 정제

| 항목 | 내용 |
| --- | --- |
| Goal | 원문의 의미와 구체성을 유지하면서 취업 준비에 활용하기 좋은 자연스러운 문장으로 정제 |
| Input | 구조화 결과, gap 답변 텍스트, `anchor_block_id` |
| Output | 블록별 정제 결과 |

**입력 유형별 처리 — 둘 다 오면 구분해서 처리합니다.**

| 입력 | 처리 |
| --- | --- |
| 구조화 결과 | 블록과 원문의 매핑을 유지한 채 원문만 정제 |
| gap 답변 (`extend_block`) | **`anchor_block_id`의 기존 텍스트와 답변을 결합**해 정제 후 해당 블록 update |

**정제 기준**

- 원문의 사실과 의미 유지. **텍스트에 없는 내용의 임의 생성은 절대 금지**
- 추상적 표현보다 구체적인 행동·맥락·사고 과정이 드러나게
- 가능한 범위에서 What / How / Why / Result가 드러나게
- 2-1의 좋은 경험정리 특징을 만족하도록
- **명사 종결**
- 화살표(`→`), 슬래시(`/`)를 활용해 구조적으로 작성

**배정은 바꿀 수 없습니다.** 정제 노드의 출력 스키마에서 배정 필드를 제거해
구조적으로 막습니다. 블록별로 나눠 호출하지 않고 **한 활동 단위로 묶어 1회 호출**하되,
입력 item 집합 == 출력 item 집합을 검증합니다.

### 5-7. Validation

검증 항목:

- content가 있으면 1~500자 (템플릿 빈 슬롯과 카테고리 컨테이너는 빈 값 허용)
- alias 존재와 사용자·활동 소유권
- 부모·target·after 존재, 같은 부모의 형제 여부
- `parent_ref`와 `parent_item_id` 동시 지정 금지 (`add`는 둘 중 하나 필수)
- 부모와 생성 블록의 위계, **5단계 초과 금지**
- **위계별 AI 권한** (1·2단계 생성 금지, 3단계 수정 금지, 전 위계 삭제 금지)
- `is_text_editable` 여부
- item 누락·중복
- 입력 사실 보존과 hallucination 금지

**회귀 분기**

```text
통과              → coordinator
위계·권한 위반    → 블록 단위 구조화
글자수 위반       → 문장 정제
```

보정은 **최대 2회**입니다. 초과한 항목은 커밋 items에서 제외하고 `dropped`에 담아
결과 응답에서 알립니다. 항목 하나 때문에 나머지 정상 블록까지 버리지 않습니다.

### 5-8. 커밋

메인 서버 `POST /commit` 호출입니다. 상세는 API 명세 4-2·4-3.

- alias → 실제 ID 역변환 후 items 전송
- commit 구간 cancellation 차단 (shield)
- `409 map_version_conflict` → 최신 맵 재조회 → 구조 유지 시 validate부터,
  구조 변경 시 structure부터 한 번 재실행. 두 번째 충돌은 `commit_conflict`
- `422 unknown_slot_id` → 카탈로그 재조회 후 1회 재시도
- 응답을 `ai_experience_request.result`에 저장

### 5-9. 결과 응답 생성

**LLM을 쓰지 않는 결정적 템플릿**입니다. 커밋 결과의 변수만 채웁니다.

> ⚠️ 아래는 **초안**입니다. 노션 「에이전트」 3.8이 미작성이라 임시로 정의했으며,
> 기획 확정 시 문구만 교체하면 되도록 변수를 분리했습니다.

**변수**

| 변수 | 출처 |
| --- | --- |
| `{experience_name}` | 대상 활동 블록의 `content` |
| `{category_label}` | 카테고리 라벨 (상세정보·주요성과·…) |
| `{added_count}` | 해당 카테고리에 추가된 블록 수 |
| `{updated_count}` | 해당 카테고리에서 수정된 블록 수 |
| `{dropped_count}` | validate 보정 초과로 제외된 항목 수 |

**단일 카테고리**

```markdown
{experience_name} > {category_label}에 {added_count}개를 정리했어요.
```

**여러 카테고리**

```markdown
{experience_name}에 정리했어요.

- {category_label} — {added_count}개 추가
- {category_label} — {updated_count}개 수정
```

**기존 블록만 수정한 경우 (gap 답변)**

```markdown
{experience_name} > {category_label} 내용을 보완했어요.
```

**탈락 항목이 있으면 뒤에 덧붙임**

```markdown
{dropped_count}개는 글자 수 제한(500자)을 넘어 넣지 못했어요. 나눠서 입력해 주세요.
```

경로(`{experience_name} > {category_label}`)를 반드시 포함합니다. 사전 승인이 없는
구조이므로 이 문구가 사용자가 오배정을 발견하는 주 경로입니다.

### 5-10. gap 분석

| 항목 | 내용 |
| --- | --- |
| Goal | 이번 턴에 커밋된 내용 기준으로 보완이 필요한 gap을 분석하고 **최대 1개** 제안 |
| Input | **이번 턴에 커밋될 items** (validate 통과 시점에 확정) |
| Output | 제안 문구, gap(있으면), gap 유형, 기준 블록 ID |

현재 맵만 보고 분석하면 방금 채운 블록을 비어 있다고 제안하게 됩니다.

**우선순위** — 높은 것부터 하나만 고릅니다.

| 순위 | 기준 | 예 |
| --- | --- | --- |
| 1 | 해당 카테고리의 핵심 정보 누락 | 담당업무 하위인데 실제 담당 업무가 불분명 |
| 2 | 사용자의 직접적인 행동 부족 | "프로젝트를 진행했다"처럼 역할이 안 드러남 |
| 3 | 수행 방식 또는 판단 기준 부족 | 어떤 기준으로 판단했는지 없음 |
| 4 | 배운 점 또는 활용 방향 부족 | 얻은 인사이트가 없음 |
| 5 | 결과 또는 변화 부족 | 실행 후 변화나 수치가 없음 |

같은 순위가 여럿이면: 이번 커밋과 가장 직접 연결되는 것 → 한 번의 답변으로 쉽게
보완할 수 있는 것 → 보완 시 활용도가 가장 크게 오르는 것.

**gap을 만들지 않는 경우**

- 이번 커밋 내용만으로 충분히 구체적
- 이번 커밋 내용과 직접 관련이 없음
- 답변해도 품질 개선 효과가 낮음
- 전체 경험 맥락을 봐야만 판단 가능

**gap 유형**

| 유형 | 조건 | 사용자 답변 시 |
| --- | --- | --- |
| `extend_block` | 기존 블록에 내용을 추가하면 되는 경우 | 문장 정제로 전달 |
| `new_child_block` | 답변을 별도 하위 블록으로 만들어야 하는 경우 | 구조화로 전달. 생성 위치는 기준 블록 하위 고정 |

**제안 문구**

- 한 문장
- 부족한 정보를 직접적으로 묻는 문장
- 부담을 주는 평가 표현 지양
- 바로 답변할 수 있도록 구체적으로

**gap이 없으면** 고정 문구를 출력합니다.

```text
더 정리하고 싶으신 내용이 있나요?
```

생성한 gap은 `ai_experience_session.active_gap`에 저장해 다음 턴에서 사용하고,
gap이 없으면 `null`로 비웁니다. **분석·생성이 실패했을 때만** 이벤트를 생략합니다.

### 5-11. Fallback

DB를 수정하지 않고 `message_complete(committed=false)`를 보낸 뒤 요청을 completed로
저장합니다. 실패가 아니므로 재시도 버튼을 노출하지 않습니다.

응답은 **진입 경로별 고정 문구**이며 LLM을 호출하지 않습니다.

| 진입 경로 | `fallback_reason` | 문구 |
| --- | --- | --- |
| Router가 기능 밖 요청으로 판정 | `out_of_scope` | 아직 지원하지 않는 기능이에요. |
| 파일에서 텍스트를 추출할 수 없음 | `file_unreadable` | 파일에서 내용을 읽지 못했어요. 다른 파일로 올려 주시거나 내용을 직접 입력해 주세요. |
| 반영할 내용이 없음 (전부 반영 제외) | `nothing_to_apply` | 정리에 반영할 내용을 찾지 못했어요. 어떤 경험을 하셨는지 알려주세요. |
| 대상 활동을 하나로 특정할 수 없음 | `ambiguous_target` | 어떤 경험에 정리할지 알려주세요. |

경로를 하나의 문구로 합치지 않는 이유는 사용자가 취할 다음 행동이 다르기
때문입니다. 손상된 파일과 기능 밖 요청에 같은 답을 주면 사용자는 파일을 바꿔
올려볼 생각을 하지 못합니다. `ambiguous_target`은 되묻는 문구여야 합니다 (6-2).

---

## 6. 블록 배정 정확성

새 내용 경로에 사전 승인이 없으므로 배정 오류 방어가 중요합니다.

| 유형 | 방어 |
| --- | --- |
| **없는 블록 지정** | 코드로 100% 차단 |
| **엉뚱한 블록 지정** | 범위 제한 + 되묻기 |

### 6-1. 별칭 화이트리스트

블록 ID(`bigint`)를 LLM에 노출하지 않고 요청마다 짧은 별칭을 부여합니다.

```text
[exp_101] 교내 커머스 리뉴얼
  [b_20] 문제해결
    [b_21] (빈 블록 — 가이드: 어떤 문제가 발생했나요?)
```

- 출력 스키마가 항목마다 참조 별칭을 **필수**로 요구합니다
- 서버가 실제 ID로 역변환하며 **매핑에 없는 별칭은 그 항목을 탈락**시킵니다
- 원본 ID를 주면 LLM이 그럴듯한 숫자를 지어내지만, 별칭은 대조로 즉시 걸러집니다

프롬프트에는 JSON이 아니라 **들여쓰기 트리 텍스트**로 렌더링합니다. 토큰이 절반
이하로 줄고 구조 파악이 쉽습니다. 빈 블록은 `(빈 블록 — 가이드: …)`로 표시해
**placeholder와 사용자 작성 내용이 절대 섞이지 않게** 합니다.

### 6-2. 범위 제한과 되묻기

- **읽기는 outline 전체 + 상세 1개**, **쓰기는 한 활동만**
- 대상이 확실하지 않으면 임의 배정을 금지하고 fallback으로 되묻습니다.
  빈칸은 되돌릴 수 있지만 잘못 배정된 내용은 사용자가 찾지 못합니다

---

## 7. LangGraph 상태와 재시도

### 7-1. 상태

```text
세션      : user/session/request ID
입력      : user message, 화면 context, view
파일      : 파일 reference, 추출 결과
라우팅    : intent, 현재 노드
맵        : map version, outline, 대상 활동, alias map
gap       : 활성 gap, 분류 결과 (gap 답변 / 새 내용 / 제외)
중간 산출 : filtered items, structured items, refined items
검증      : validation errors, 보정 횟수
실패      : failed node, node retry count
```

- `thread_id = session_id`, `checkpoint_ns = experience_map`
- 요청 시작 시 turn 전용 필드 초기화
- checkpoint에는 직렬화 가능한 값만 저장
- 성공 뒤 파일 reference와 큰 중간 산출물 정리

### 7-2. 재시도 두 종류

| 구분 | 주체 | 방식 |
| --- | --- | --- |
| 노드 자동 재시도 (1회) | 그래프 | `RetryPolicy(max_attempts=2)`. gap 분석·제안·커밋에는 미적용 |
| 유저 재시도 버튼 | 사용자 | `failed_node`부터 재진입. 성공한 노드 산출물 재사용 |

유저 재시도는 처음부터 돌리지 않습니다. 실패 시점 checkpoint를 불러와
`ainvoke(None, config)`로 이어서 실행합니다.

LLM client의 `max_retries`는 0으로 고정해 중복 retry를 막습니다.

### 7-3. 상태 기준

**`ai_experience_request`가 API 상태의 유일한 기준입니다.** checkpoint status를
API 상태로 사용하지 않습니다.

---

## 8. 구현 구조

```text
app/
├── api/v1/experience_map.py
└── schemas/experience_map.py

features/experience_map/
├── __init__.py
├── config.py
├── templates.py          # 카탈로그 조회·캐시
├── graph.py
├── service.py
├── coordinator.py        # 커밋·gap 병렬 처리
├── repository.py         # 경험 맵 읽기, 세션·요청
├── main_client.py        # 커밋·템플릿 API 클라이언트
├── upload_store.py
├── schemas.py
├── state.py
├── errors.py
├── nodes/
│   ├── router.py
│   ├── file_processor.py
│   ├── content_filter.py
│   ├── gap_resolver.py
│   ├── structure.py
│   ├── refine.py
│   ├── validate.py
│   ├── commit.py
│   ├── result_response.py
│   ├── gap_analysis.py
│   ├── suggestion_response.py
│   └── fallback.py
└── prompts/
    ├── router.py
    ├── file_processor.py
    ├── content_filter.py
    ├── gap_resolver.py
    ├── structure.py
    ├── refine.py
    └── gap_analysis.py

tests/
├── test_app/test_api/test_v1/test_experience_map.py
└── test_features/test_experience_map/
```

`fallback.py`와 `result_response.py`는 LLM을 쓰지 않으므로 프롬프트가 없습니다.

### 8-1. structured output 스키마

```python
class RouterOutput(BaseModel):
    intent: Literal["chat_input", "out_of_scope"]   # file_input은 코드 판정
    reason: str


class FilteredItem(BaseModel):
    item_id: str
    text: str
    source: Literal["message", "file"]


class ContentFilterOutput(BaseModel):
    gap_answer_items: list[FilteredItem]
    new_items: list[FilteredItem]
    excluded_reasons: list[str]


class StructuredItem(BaseModel):
    item_id: str
    action: Literal["add", "update"]
    parent_ref: str | None          # 기존 블록 별칭 → 커밋의 parent_id
    parent_item_id: str | None      # 같은 요청의 앞선 item_id

    target_ref: str | None          # update 시
    section_kind: SectionKind | None
    slot_id: str | None
    text: str | None                # 빈 슬롯은 None
    after_ref: str | None


class RefinedItem(BaseModel):
    item_id: str
    refined_text: str | None        # 배정 필드 없음


class GapOutput(BaseModel):
    gap: Gap | None
    message: str
```

`SectionKind`는 `DETAIL` / `ACHIEVEMENT` / `TASK` / `PROBLEM_SOLVING` / `LEARNING`
(API 명세 4-2).

---

## 9. 개발 순서

### 1. 메인 DB migration

구현:

- `experience_map`
- `ai_experience_session` (`active_gap jsonb` 포함)
- `ai_experience_request`
- `ai_commit_log`
- `block.placeholder` 컬럼
- 세션당 running 요청 1개 partial unique index
- block 길이·위계·kind 제약
- 에디터 변경·AI 커밋·되돌리기 세 경로 모두에서 map version 1회 증가
- 에디터 변경 시 해당 사용자 `ai_commit_log` 삭제
- **AI 서버 DB 계정은 읽기 전용** (`block`·`block_kind`·`experience_map` SELECT만,
  `ai_experience_*`만 쓰기)

검증:

- 같은 세션에서 running 요청 두 건을 만들 수 없음
- 메인 에디터 변경과 AI 커밋이 같은 map row를 잠금
- DB 제약 위반 시 전체 transaction rollback

### 2. DB 연결 분리

구현:

- `common/db/connection.py`의 asyncpg pool을 앱 lifespan에서 생성·종료
- `/health`에 경험 맵 DB 연결 상태 추가
- `common/checkpointer/factory.py`에서 `DATABASE_URL` fallback 제거
- `CHECKPOINT_DATABASE_URL` 미설정 시 시작 실패
- pool 크기와 statement timeout 설정

검증:

- 경험 맵 DB에 LangGraph checkpoint 테이블이 생성되지 않음
- 앱 종료 시 두 DB pool이 모두 닫힘

### 3. 설정·스키마·오류

구현:

- 환경변수 설정 모델
- 템플릿 카탈로그 조회·캐시 (`GET /templates`, 기동 1회 + 1시간 TTL)
- API request/response 모델
- 8-1의 structured output 모델
- add·update operation 모델 (`section_kind`·`slot_id` 포함)
- `active_gap` 모델
- SSE event 모델
- feature exception과 HTTP/SSE 오류 매핑
- 공통 API key·티켓 오류 응답 포맷
- 경험정리 LLM client의 내장 retry 0, 노드별 timeout 설정

검증:

- UUID, 십진 문자열 ID, 조건부 필수값 검증
- content 조건부 필수 검증 (템플릿 빈 슬롯·카테고리 컨테이너는 생략)
- 카탈로그 캐시 만료·`unknown_slot_id` 시 재조회
- API 명세 예시와 직렬화 결과 일치

### 4. 세션·요청 Repository

구현:

- `get_or_create_session(user_id)`
- `get_session(session_id)`
- `claim_request(session_id, request_id, request_hash, input_meta)`
- `renew_request_lease(...)`
- `get_request(...)`
- `mark_request_failed(...)`
- `mark_request_completed(...)`
- 만료된 running 요청 정리
- 30일이 지난 완료 요청 정리

규칙:

- `ai_experience_request`가 API 상태의 유일한 기준
- checkpoint status를 API 상태로 사용하지 않음
- 같은 request ID와 같은 hash는 저장 상태 반환
- 같은 request ID와 다른 hash는 충돌
- 같은 running request의 중복 stream은 409, completed request는 저장 이벤트 재전송
- 새 요청을 시작하면 이전 failed 요청의 사용자 재시도 비활성화
- 30초 주기의 lease 갱신 task가 실패하면 실행을 중단하고 failed 저장

검증:

- 여러 worker에서 같은 세션을 동시에 실행해도 하나만 성공
- 프로세스 중단 뒤 lease 만료로 복구
- 다른 사용자 세션·요청 접근 차단

### 5. 경험 맵 Repository와 커밋 클라이언트

구현:

- `get_map(user_id)` — **읽기 전용**
- flat block 목록을 정렬된 tree로 변환
- 그룹·활동 outline 생성
- 선택 활동 full context 생성
- 실제 ID ↔ LLM alias 변환
- map version 조회

**커밋은 DB 쓰기가 아니라 메인 서버 API 호출입니다** (`main_client.py`).
위계 검증·`level`·`position`·`kind` 계산·`placeholder` 부여·`ai_commit_log`는
전부 메인 서버 몫이며 AI 서버에서 제거합니다.

커밋 클라이언트 규칙:

- `POST /commit`에 `user_id`·`request_id`·`base_map_version`·items 전달
- `409 map_version_conflict` → 맵 재조회 후 재구성 판단 (16번)
- `422 unknown_slot_id` → 템플릿 카탈로그 재조회 후 1회 재시도
- 응답을 `ai_experience_request.result`에 저장

검증:

- version 충돌을 일반 오류와 구분
- 같은 `request_id` 재호출 시 기존 commit 결과 반환
- 커밋 응답 유실 시 `GET /commit/{request_id}`로 복구

### 6. LangGraph 상태와 checkpoint

7-1의 상태를 구현합니다.

검증:

- 실패 superstep을 `ainvoke(None, config)`로 이어서 실행
- 이전 대화는 유지되고 새 요청의 중간 필드는 섞이지 않음

### 7. 임시 첨부 파일 저장

구현:

- TXT·DOCX·PPTX·PDF·PNG·JPEG MIME, 확장자, file signature 검사 (`.txt`는 UTF-8 디코딩)
- 요청당 최대 3개, 파일당 최대 10MB
- 업로드 중 SHA-256 계산
- GCS request 전용 임시 object 업로드
- request claim 실패 또는 저장 결과 재전송이면 방금 올린 object 즉시 삭제
- 추출 성공 즉시 삭제
- 추출 실패 object 1시간 TTL
- 만료 object 정리 job 또는 bucket lifecycle

검증:

- 다른 worker에서 추출 재시도 가능
- 추출 실패 후 1시간 안에는 원본으로 재시도
- 파일명·본문·추출 원문이 로그에 남지 않음

### 8. API·SSE 뼈대

구현 API:

```text
POST /api/v1/experience-map/sessions
GET  /api/v1/experience-map/sessions/{session_id}/state
POST /api/v1/experience-map/sessions/{session_id}/chat/stream
POST /api/v1/experience-map/sessions/{session_id}/retry/stream
GET  /api/v1/experience-map/sessions/{session_id}/requests/{request_id}
```

구현:

- `app/api/v1/__init__.py`에 router 등록
- **티켓 검증 미들웨어** (서명 → 만료 → `sid` == path `session_id`)
- 티켓 검증을 요청 body 읽기 전에 수행
- CORS에 웹 오리진과 `Authorization` preflight 추가
- 티켓 `sub` 단위 rate limit
- 10초 heartbeat
- stream 시작 전 JSON 오류, 시작 후 SSE 오류
- `processing_started`, `node_status`, `commit_result`, `message_complete`
- `suggestion_ready`, `processing_complete`, `error`, `ping`
- `langgraph.json`에 경험정리 graph 등록

검증:

- mock graph로 전체 API 계약 테스트
- 위조·만료 티켓과 `sid` 불일치 차단
- 잘못된 업로드는 stream을 열기 전에 거부
- 브라우저에서 직접 SSE 연결

### 9. Router

5-1을 구현합니다.

### 10. 파일처리

5-2를 구현합니다.

검증:

- 추출 완료 뒤에는 원본 파일 없이 재시도
- 추출 노드 실패 시에는 GCS 원본으로 재시도
- 파서 형식과 OCR 형식을 섞어 업로드해도 순서 유지

### 11. 반영 내용 필터링

5-3을 구현합니다.

검증:

- structured output schema 검증
- 모든 출력 item을 원문 source로 역추적
- `active_gap`이 없을 때 gap 답변으로 분류되지 않음

### 12. 대상 활동 선택

5-4를 구현합니다.

검증:

- 다른 사용자·다른 활동 alias 사용 차단
- 대상이 불명확한 상태에서 DB 변경 없음

### 13. 블록 구조화

5-5를 구현합니다.

검증:

- structure 전후 item 집합 동일
- 1·2단계 생성과 편집 불가 block 수정 차단
- 이미 있는 카테고리를 중복 생성하지 않음
- 입력 텍스트가 그대로 유지됨 (구체성 손실 없음)
- 생성한 블록마다 올바른 `slot_id`가 지정됨
- 문제해결 5단계 템플릿 6종 선택이 내용과 일치
- 템플릿 사용 시 정보가 없는 슬롯도 빈 블록으로 생성됨
- 템플릿 미사용 시 빈 블록이 생성되지 않음

### 14. 문장 정제

5-6을 구현합니다.

검증:

- 정제 전후 operation metadata 동일
- 원문 근거가 없는 수치·고유명사 생성 차단
- gap 결합 시 기존 내용이 유실되지 않음
- 명사 종결과 구조적 표기 적용

### 15. validate와 보정 loop

5-7을 구현합니다.

`graph.py`에서 분기를 연결하고 PostgreSQL checkpointer로 compile합니다. 자동 재시도
대상 노드에는 `RetryPolicy(max_attempts=2)`를 적용하고 gap 분석·제안·커밋에는
적용하지 않습니다. Fallback과 validate 성공 경로는 graph를 종료하고 service
coordinator가 후속 응답 또는 커밋을 처리합니다.

### 16. 커밋 위임과 version 충돌 복구

5-8을 구현합니다.

검증:

- SSE가 끊겨도 commit 중복 실행 없음 (메인이 `request_id` 기준 멱등)
- 커밋 성공 후 응답이 유실돼도 `GET /commit/{request_id}`로 복구

### 17. 결과·gap 병렬 coordinator

LangGraph의 병렬 branch는 같은 superstep이 모두 끝난 뒤 다음 step으로 넘어갑니다.
따라서 `커밋 → 결과 응답`과 `gap 분석 → 제안 응답`을 한 graph fan-out으로
연결하지 않습니다.

```text
validate 결과 수신
→ commit task와 gap task 동시 시작
→ commit task await
   ├→ 실패: gap task 취소 → failed/error
   └→ 성공: commit_result → result message
→ gap task await
   ├→ 실패: 이벤트 생략
   └→ 성공: suggestion_ready → suggestion message
→ request completed → processing_complete
```

두 task는 서로 다른 state 필드에만 씁니다. 결과 문구는 5-9의 결정적 템플릿,
제안 문구만 LLM입니다.

검증:

- 느린 gap 분석이 결과 응답 전송을 지연하지 않음
- gap 실패가 완료 요청을 failed로 바꾸지 않음
- 커밋 실패 뒤 suggestion 이벤트가 전송되지 않음
- 방금 커밋한 내용을 누락으로 지적하지 않음
- gap 없음과 gap 분석 실패가 구분됨

### 18. Fallback

5-11을 구현합니다.

검증:

- 진입 경로 4가지가 각각 자기 문구를 내보냄 (문구 하나로 합쳐지지 않음)
- 어느 경로든 DB 변경 없이 completed로 저장되고 재시도 버튼이 노출되지 않음

### 19. 사용자 재시도

구현:

- 마지막 요청, failed 상태, retryable, 30분 TTL 확인
- 텍스트 추출 미완료면 GCS object TTL 추가 확인
- request lease 재획득
- 최신 경험 맵과 version 재조회
- checkpoint의 실패 superstep부터 resume
- commit 결과가 이미 있으면 commit을 건너뛰고 completed로 복구

검증:

- 성공한 이전 노드는 다시 실행하지 않음
- 새 요청 시작 뒤 이전 실패 요청 재시도 거부
- 만료 상태는 `410 retry_expired`

### 20. 연결 종료와 복구

구현:

- 연결 종료가 커밋 전이면 실행 취소 후 failed 저장
- commit task는 shield 처리
- 연결 종료가 커밋 후면 suggestion을 생략하고 completed 저장
- request GET API로 저장 결과 복구
- **만료 lease를 정리할 때 `GET /commit/{request_id}`를 먼저 확인**
  - `committed: true` → 결과를 채우고 completed
  - `committed: false` → retryable failed
- lease가 살아 있으면 `running` 유지. 프론트는 폴링하고 재시도 버튼은 `failed`에서만 노출

검증:

- 파일처리·LLM·커밋 각 시점에서 연결을 끊는 테스트
- 재접속 뒤 중복 block 없이 같은 결과 조회
- 커밋 성공 + 응답 유실 상태가 lease 만료 후 completed로 정리됨

### 21. 되돌리기 연동

메인 서버 구현 (AI 서버 작업 없음):

- 인증 사용자와 최신 AI commit log 확인
- 24시간과 map version 확인
- 생성 block을 자식부터 삭제
- update 이전 content 복원
- map version 1회 증가
- 성공 후 commit log 삭제

검증:

- AI 커밋 뒤 사용자가 편집했으면 409
- 최신 AI 커밋만 되돌리기 가능
- 반복 호출로 두 번 되돌리지 않음

### 22. 테스트와 운영 검증

단위 테스트:

- 각 structured output schema
- 모든 graph 분기
- validate 보정 loop와 최대 횟수
- alias 소유권·위계·content 제약
- 위계별 AI 권한 (1·2단계 생성 차단, 3단계 수정 차단)
- 필터링 3분류와 후속 노드 결정
- 생성 위치별 템플릿·`slot_id` 선택
- 티켓 검증 (서명·만료·`sid` 불일치)
- request hash와 idempotency
- SSE 이벤트 순서

DB·연동 통합 테스트:

- session 생성 경쟁
- running request 경쟁
- 커밋 API 멱등 (같은 `request_id` 재호출)
- `409 map_version_conflict` 1회 복구와 최종 실패
- 커밋 성공 후 응답 유실 → `GET /commit/{request_id}` 복구
- `422 unknown_slot_id` → 카탈로그 재조회 후 재시도
- revert 성공·충돌·만료

시나리오 테스트:

- 파일(파서) → 새 block 추가
- 파일(OCR) → 새 block 추가
- 채팅 → 새 block 추가
- gap 답변 → refine 분기 (기존 블록 결합)
- gap 답변 → structure 분기 (하위 블록 생성)
- gap 답변 + 새 내용 동시 입력
- 없는 3단계 카테고리 생성
- 담당업무 템플릿으로 4·5단계 생성
- 문제해결 템플릿 6종 중 선택하여 5단계 생성
- 기능 밖 fallback
- 추출 불가 파일 → fallback
- 노드 실패 → 사용자 재시도
- gap 분석 실패 → 결과만 응답
- SSE 단절 → request 결과 조회

마지막 실행:

```bash
ruff check .
ruff format --check .
pytest
```

---

## 10. 남은 결정

| # | 항목 | 주체 | 영향 |
| --- | --- | --- | --- |
| ~~1~~ | ~~`slot_id` 전체 목록~~ | ~~기획·메인 서버~~ | **해소됨 (2026-08-09)** — 3-0·3-2·3-3·3-4에 반영 |
| 2 | 결과 응답 문구 확정 (5-9는 초안) | 기획 | 변수 구조는 유지되므로 문구만 교체 |
| 3 | 템플릿 카탈로그 본체 위치 (코드 상수 vs DB) | 메인 서버 | AI 서버 영향 없음 |

메인 서버에서 전달받아 확정된 카탈로그는 level 4 슬롯 10개 + level 5 슬롯 28개
(담당업무 1종 × 4 + 문제해결 6종 × 4) = **38개**입니다.

**level에 따라 형식이 다릅니다** — 4단계는 2-part(`{SECTION}.{SLOT}`), 5단계는
3-part(`{SECTION}.{TEMPLATE}.{SLOT}`)입니다 (3-0).
