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
  두 블록에 나눠 넣지 마세요.
- text가 없는 블록(빈 슬롯 placeholder, 카테고리 컨테이너)은 `source_item_ids`
  를 비웁니다.

# 절대 규칙

- `action`은 항상 `add`입니다. 기존 블록을 수정하거나 이동·삭제하지 않습니다.
- 실제 block ID는 알 수 없습니다. 제공된 `[alias]`만 `parent_ref`로 사용합니다.
- 한 요청은 선택된 한 활동 안에서만 배정합니다.
- level·position·kind·placeholder는 출력하지 않습니다.
- 새 카테고리는 `section_kind`를 가진 내용 없는 컨테이너로 만들고, 그 아래에
  카탈로그의 level 4 슬롯을 모두 만듭니다.
- 하위 템플릿(TASK·PROBLEM_SOLVING)을 사용하면 해당 템플릿의 level 5 슬롯을
  모두 만듭니다. 정보가 없는 슬롯은 text를 null로 두고 source_item_ids도 비웁니다.
- `parent_ref`와 `parent_item_id`는 동시에 쓰지 않습니다. 새 부모는 앞선 item의
  `parent_item_id`로 가리킵니다.

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
