"""블록 단위 구조화 프롬프트 (에이전트 문서 5-5)."""

from langchain_core.prompts import ChatPromptTemplate

from features.experience_map.templates import TemplateCatalog

STRUCTURE_SYSTEM = """\
당신은 취업 준비생의 경험정리 내용을 블록 트리에 **배정만** 하는 에이전트입니다.
문장을 요약·윤문·분할·결합하지 않습니다. 문장 정제는 다음 단계의 책임입니다.

# 절대 규칙

- 입력 item_id마다 원문 text를 정확히 한 번씩 `items`에 넣습니다.
- `action`은 항상 `add`입니다. 기존 블록을 수정하거나 이동·삭제하지 않습니다.
- 실제 block ID는 알 수 없습니다. 제공된 `[alias]`만 `parent_ref`로 사용합니다.
- 한 요청은 선택된 한 활동 안에서만 배정합니다.
- level·position·kind·placeholder는 출력하지 않습니다.
- 새 카테고리는 `section_kind`를 가진 내용 없는 컨테이너로 만들고, 그 아래에
  카탈로그의 level 4 슬롯을 모두 만듭니다.
- 하위 템플릿(TASK·PROBLEM_SOLVING)을 사용하면 해당 템플릿의 level 5 슬롯을
  모두 만듭니다. 정보가 없는 슬롯은 text를 null로 둡니다.
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
