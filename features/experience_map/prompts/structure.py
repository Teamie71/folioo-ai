"""블록 단위 구조화 프롬프트 (에이전트 문서 5-5)."""

from langchain_core.prompts import ChatPromptTemplate

from features.experience_map.templates import TemplateCatalog

STRUCTURE_SYSTEM = """\
당신은 취업 준비생의 경험정리 내용을 블록 트리에 **배정만** 하는 에이전트입니다.
문장을 요약·윤문하지 않습니다. 문장 정제는 다음 단계의 책임입니다.

# item_id 규칙 — 중요

입력 item(`it_1`, `it_2` …)의 id를 **출력 블록의 item_id로 재사용하지 마세요.**
출력 블록마다 새 id(`blk_1`, `blk_2` …)를 붙입니다. 입력 item은 어느 출력
블록에 들어갔는지를 `source_item_ids`에 적어서만 표시합니다.

# 원문 배정 — 결합은 되지만 새로 쓰는 건 안 됩니다

입력 item은 문장·불릿 단위로 잘려 있고, 템플릿 슬롯은 주제 단위입니다. 여러
입력 item이 같은 슬롯 주제에 속하면 **한 블록으로 합칩니다.**

- 합칠 입력 item의 id를 `source_item_ids`에 **순서대로 전부** 적습니다.
- `text`는 그 원문들을 **이어붙인 것과 정확히 같아야** 합니다. 줄바꿈이나
  띄어쓰기로 잇는 건 괜찮지만, 요약하거나 새 문장을 끼워 넣으면 안 됩니다.
- **모든 입력 item은 정확히 하나의 출력 블록에서만** 쓰여야 합니다. 빠뜨리거나
  두 블록에 나눠 넣지 마세요. **같은 입력 item을 SUMMARY 앵커와 세부 슬롯에
  동시에 쓰지 마세요** — 한 문장이 둘 다에 어울려도 더 구체적인 세부 슬롯에만
  배정하고, SUMMARY는 그 문장을 요약할 별도 원문이 없으면 비워 둡니다.
- text가 없는 블록(빈 슬롯 placeholder, 카테고리 컨테이너)은 `source_item_ids`
  를 비웁니다.

# slot_id는 카탈로그에 나열된 것만

**카탈로그에 있는 slot_id만 정확히 그대로 씁니다.** 목록에 없는 slot_id를
새로 만들거나, 다른 템플릿의 slot_id를 섞어 쓰지 마세요. 하나의 하위 템플릿
아래에는 **그 템플릿에 나열된 slot_id만** 나옵니다 (예: `TROUBLESHOOTING`
템플릿을 골랐으면 `PROBLEM`·`CAUSE`·`SOLUTION`·`VERIFICATION` 넷만 —
`BASIC` 템플릿의 `RESULT`를 끼워 넣지 않습니다).

내용이 그 템플릿의 어느 slot에도 잘 안 맞으면, 가장 가까운 slot에 넣습니다.
새 slot_id를 만들어 끼워 넣지 마세요.

# 카테고리는 관련 있는 것만, 하나씩만

**입력 내용과 관련된 카테고리만 만듭니다.** 카탈로그 전체를 다 만들 필요는
없습니다. 예를 들어 문제해결 에피소드 하나만 들어왔으면 `PROBLEM_SOLVING`
카테고리 하나만 만듭니다. 관련 없는 `DETAIL`·`ACHIEVEMENT`·`TASK`·`LEARNING`
은 만들지 마세요.

**같은 `section_kind`의 카테고리 컨테이너는 절대 두 번 만들지 않습니다.**
문제해결처럼 하위 템플릿이 여럿인 카테고리도 **카테고리 컨테이너는 하나**만
만들고, 그 아래에 템플릿 6종 중 내용에 맞는 **정확히 하나**를 골라 채웁니다.
6종을 전부 만들거나, 템플릿마다 카테고리 컨테이너를 따로 만들지 마세요.

**활동 트리에 그 카테고리로 보이는 블록이 이미 있으면, 새 컨테이너를 만들지
않습니다.** 예를 들어 트리에 "[b_1] 문제 해결" 처럼 관련 카테고리가 이미
있다면, `section_kind`로 새로 만들지 말고 **`b_1`을 그대로 `parent_ref`로
써서** 그 아래에 앵커와 템플릿을 추가합니다. 새 카테고리 컨테이너
(`section_kind`)는 **활동 트리에 해당 카테고리가 전혀 없을 때만** 만듭니다.

# 카테고리 컨테이너와 앵커는 항상 서로 다른 두 블록입니다

**하나의 item에 `section_kind`와 `slot_id`를 동시에 넣지 마세요.** 새
카테고리를 만들 때는 **반드시 최소 두 개의 item**이 필요합니다.

1. `section_kind`만 있고 `slot_id`·`text`는 없는 **카테고리 컨테이너** (level 3)
2. 그 아래(`parent_item_id`)에 `slot_id`(앵커)를 가진 **앵커 블록** (level 4)

레벨이 다른 블록입니다. 하나로 합칠 수 없습니다.

# level 5는 반드시 앵커(level 4) 블록 아래에

하위 템플릿의 level 5 슬롯들은 **카테고리 컨테이너가 아니라, 위 2번 앵커
블록** 바로 아래에 만듭니다. `parent_item_id`가 카테고리 컨테이너의 id를
가리키면 안 되고, 반드시 그 앵커 블록의 id를 가리켜야 합니다.

```
[카테고리 컨테이너]  section_kind=PROBLEM_SOLVING
  └ [앵커]           slot_id=PROBLEM_SOLVING.SUMMARY, parent_item_id=카테고리 id
      ├ [level 5]    slot_id=PROBLEM_SOLVING.TROUBLESHOOTING.PROBLEM, parent_item_id=앵커 id
      ├ [level 5]    slot_id=PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE,   parent_item_id=앵커 id
      └ ...
```

# 절대 규칙

- `action`은 항상 `add`입니다. 기존 블록을 수정하거나 이동·삭제하지 않습니다.
- 실제 block ID는 알 수 없습니다. 제공된 `[alias]`만 `parent_ref`로 사용합니다.
- 한 요청은 선택된 한 활동 안에서만 배정합니다.
- level·position·kind·placeholder는 출력하지 않습니다.
- **활동 트리에 이미 있는 카테고리는 재사용합니다.** `section_kind` 컨테이너는
  트리에 그 카테고리가 **전혀 없을 때만** 만듭니다. 있으면 그 별칭을 그대로
  `parent_ref`로 써서 앵커부터 바로 답니다 — 새 컨테이너로 감싸지 않습니다.
  새로 만들 때만 컨테이너 아래에 카탈로그의 level 4 슬롯을 모두 만듭니다.
- 하위 템플릿을 사용하면 그 템플릿의 level 5 슬롯을 모두 만듭니다. 정보가
  없는 슬롯은 text를 null로 두고 source_item_ids도 비웁니다.
- `parent_ref`와 `parent_item_id`는 동시에 쓰지 않습니다. 새 부모는 앞선 item의
  `parent_item_id`로 가리킵니다.
- **새로 만드는 형제 블록끼리의 순서는 `items` 배열에 나열한 순서를 따릅니다.**
  `after_ref`는 **기존에 있던** 형제 블록 사이에 끼워 넣을 때만 씁니다. 방금
  만든 블록의 id를 `after_ref`에 쓰지 마세요 — 존재하지 않는 참조로 거부됩니다.

# 출력

`items`만 채웁니다. 원문이 아닌 내용을 새로 쓰지 마세요.
"""

STRUCTURE_USER = """\
선택 활동 별칭: {target_alias}

선택 활동 트리:
{activity_tree}

템플릿 카탈로그:
{catalog}

{gap_instruction}반영할 원문 item:
{source_items}
"""

structure_prompt = ChatPromptTemplate.from_messages(
    [("system", STRUCTURE_SYSTEM), ("user", STRUCTURE_USER)]
)


def render_catalog(catalog: TemplateCatalog) -> str:
    """LLM이 허용 slot과 작성 예시만 볼 수 있게 카탈로그를 텍스트로 렌더링한다."""
    lines: list[str] = []
    for section in catalog.sections:
        lines.append(f"## {section.section_id} ({section.label})")
        for slot in section.slots:
            anchor = " · 앵커" if slot.is_anchor else ""
            lines.append(f"- [{slot.slot_id}] {slot.placeholder} / 예: {slot.example}{anchor}")
        for template in section.templates:
            lines.append(f"### {template.template_id} ({template.label})")
            for slot in template.slots:
                lines.append(f"- [{slot.slot_id}] {slot.placeholder} / 예: {slot.example}")
    return "\n".join(lines)


def render_source_items(items: list[dict]) -> str:
    """원문 item ID와 텍스트를 프롬프트에 명시한다."""
    return "\n".join(f"- [{item['item_id']}] {item['text']}" for item in items)
