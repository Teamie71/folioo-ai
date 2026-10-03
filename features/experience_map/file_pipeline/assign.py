"""파일 줄을 템플릿 칸에 배정한다: 문서 관문 ∥ 칸 배정 → 제외 확인 → 정리.

LLM은 줄 번호별로 slot·에피소드만 고르고 원문은 다시 쓰지 않는다. 줄을 버리는 건
(1) 코드가 확실한 노이즈로 판단했거나, (2) LLM이 제목이라 하고 코드 기준으로도 제목
모양이거나, (3) LLM이 무관하다고 하고 확인 호출에서도 무관하다고 한 경우뿐이다 —
LLM에게 버리는 권한을 그대로 주면 실제 상세정보를 통째로 버린 적이 있다.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from common.llm import get_experience_map_llm
from features.experience_map.config import get_settings
from features.experience_map.file_pipeline.split import (
    heading_like,
    is_section_name,
    project_number,
)
from features.experience_map.nodes.refine import _bigram_overlap_ratio
from features.experience_map.prompts.file_assign import (
    EXCLUDE_SLOT,
    HEADING_SLOT,
    INSTRUCTION_SLOT,
    REQUESTED_EXCLUDE_REASON,
    assign_prompt,
    document_gate_prompt,
    exclude_verify_prompt,
    reassign_prompt,
    requested_exclude_verify_prompt,
)
from features.experience_map.prompts.structure import render_catalog
from features.experience_map.templates import TemplateCatalog

logger = logging.getLogger(__name__)

MESSAGE_ID_PREFIX = "m_"
"""채팅 메시지 줄 id 접두사. 파일 줄은 `it_`."""

CHUNK_LINES = 40
"""한 번의 배정 호출에 맡기는 줄 수. 넘으면 제목 경계에서 나눠 병렬로 부른다."""

LISTING_GAP = 4
"""프로젝트 제목 줄이 이 줄 수 안에 또 있으면 목차로 보고 그 사이에서 청크를 자르지 않는다."""

GATE_MAX_CHARS = 12_000
_PROBLEM_SENTENCE = re.compile(r"(?:시오|것은|의 값|\?)[\s.!]*$")
"""문제지의 문제·지시문. 경험의 근거 문장으로 인정하지 않는다."""
"""문서 관문에 보내는 원문 길이 상한. 경험 문서인지 판단하는 데는 앞부분으로 충분하다."""


class LineAssignment(BaseModel):
    id: str
    slot_id: str = Field(
        ..., description="카탈로그 slot_id, 문서 제목이면 HEADING, 경험 서술이 아니면 EXCLUDE"
    )
    episode: str | None = Field(
        None, description="담당업무·문제해결이면 그 업무/에피소드의 첫 줄 id"
    )
    reason: str | None = Field(
        None, description="EXCLUDE일 때만: 사용자요청/자기소개/개인정보/문서메타/무관"
    )


class AssignOutput(BaseModel):
    items: list[LineAssignment]
    only_sections: list[str] = Field(
        default_factory=list,
        description="메시지가 '문제해결만 정리해줘'처럼 일부 구획만 정리하라고 하면 그 section_id 목록",
    )
    excluded_sections: list[str] = Field(
        default_factory=list,
        description="메시지가 '배운 점은 빼줘'처럼 구획을 빼라고 하면 그 section_id 목록",
    )


class DocumentGate(BaseModel):
    evidence: list[str] = Field(
        default_factory=list,
        description="작성자가 직접 한 일·맡은 역할·배운 점을 서술한 원문 문장을 최대 3개 그대로 "
        "인용. 문제·지문·공고·설명 문장은 넣지 않는다. 없으면 빈 목록",
    )
    is_experience: bool = Field(
        ..., description="작성자 본인이 직접 수행한 활동·프로젝트·업무 경험을 서술하면 true"
    )
    doc_type: str = Field(
        ..., description="경험 정리/이력서/포트폴리오/시험지/채용공고/강의자료 등"
    )
    reason: str


class _Verdict(BaseModel):
    id: str
    keep: bool = Field(..., description="이 경험의 내용이 조금이라도 있으면 true")


class _VerifyOutput(BaseModel):
    items: list[_Verdict]


@dataclass
class FileAssignment:
    """배정 결과. `assignments`는 반영할 줄만, `excluded`는 무관해서, `requested_excluded`는
    사용자 지시로 뺀 줄."""

    is_experience: bool
    doc_type: str
    lines: dict[str, str]
    assignments: dict[str, dict] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)
    requested_excluded: list[str] = field(default_factory=list)


def _grounded(evidence: list[str], lines: list[str]) -> bool:
    """관문이 든 근거 문장 중 하나라도 원문에 실제로 있고 문제·지시문이 아닌지 본다.

    모델이 인용하며 불릿·문장부호를 바꾸는 일이 잦아, 원문 줄과 글자쌍이 80% 이상
    겹치면 있는 것으로 본다.
    """
    document = re.sub(r"\s+", "", "\n".join(lines))
    for quote in evidence:
        body = re.sub(r"^[\s\-•·*\d.)]+|[\s.。!]+$", "", quote)
        if len(re.sub(r"\s+", "", body)) < 8 or _PROBLEM_SENTENCE.search(quote.strip()):
            continue
        if re.sub(r"\s+", "", body) in document or any(
            _bigram_overlap_ratio(body, line) >= 0.8 for line in lines
        ):
            return True
    return False


def _chunks(line_ids: list[str], lines: dict[str, str]) -> list[list[str]]:
    """긴 문서를 제목 줄 경계에서 약 CHUNK_LINES줄씩 자른다.

    프로젝트 제목 줄("Project 3. …")이 있으면 그 앞에서 먼저 자른다. 프로젝트 중간에서
    자르면 다음 청크가 소제목을 새 업무로 열었다(포트폴리오 PDF 로컬 재현). 프로젝트
    제목이 몇 줄 간격으로 몰린 목차 안에서는 자르지 않는다 — 목차가 둘로 나뉘면 뒤쪽
    목차 줄이 엉뚱한 프로젝트에 붙었다.
    """
    if len(line_ids) <= CHUNK_LINES:
        return [line_ids]
    project_rows = [i for i, line_id in enumerate(line_ids) if project_number(lines[line_id])]

    def in_listing(index: int) -> bool:
        return any(0 < abs(row - index) <= LISTING_GAP for row in project_rows)

    chunks: list[list[str]] = []
    current: list[str] = []
    for index, line_id in enumerate(line_ids):
        text = lines[line_id]
        listing = in_listing(index)
        at_project = project_number(text) is not None and not listing
        at_heading = heading_like(text) and not listing
        if (
            (len(current) >= CHUNK_LINES * 0.4 and at_project)
            or (len(current) >= CHUNK_LINES * 0.6 and at_heading)
            or len(current) >= CHUNK_LINES * 1.5
        ):
            chunks.append(current)
            current = []
        current.append(line_id)
    if current:
        chunks.append(current)
    return chunks


def _render(lines: dict[str, str], line_ids: list[str]) -> str:
    return "\n".join(f"[{line_id}] {lines[line_id]}" for line_id in line_ids)


async def _assign_chunk(
    llm,
    catalog: TemplateCatalog,
    lines: dict[str, str],
    chunk: list[str],
    context: list[str],
    message_context: list[str],
    scope: dict,
) -> dict[str, LineAssignment]:
    """한 청크를 배정하고, 빠지거나 잘못 배정된 줄만 한 번 더 요청한다.

    `message_context`는 다른 청크에서 배정하는 채팅 메시지다. 이 청크에서도 메시지
    지시("2번 프로젝트만")를 알아야 범위 밖 줄을 뺄 수 있어 문맥으로만 보여준다.
    """
    rendered_catalog = render_catalog(catalog)
    document = (
        "".join(f"(앞 문맥 제목) {heading}\n" for heading in context)
        + "".join(f"(사용자 메시지, 배정 대상 아님) {text}\n" for text in message_context)
        + _render(lines, chunk)
    )
    chain = assign_prompt | llm.with_structured_output(AssignOutput)
    output: AssignOutput = await chain.ainvoke({"catalog": rendered_catalog, "document": document})
    chunk_ids = set(chunk)
    by_id = {item.id: item for item in output.items if item.id in chunk_ids}
    scope.update(
        only=set(scope.get("only", set())) | set(output.only_sections),
        excluded=set(scope.get("excluded", set())) | set(output.excluded_sections),
    )

    def invalid(item: LineAssignment) -> bool:
        if item.slot_id == INSTRUCTION_SLOT:
            return not item.id.startswith(MESSAGE_ID_PREFIX)
        if item.slot_id == HEADING_SLOT:
            return not heading_like(lines[item.id])
        if item.slot_id == EXCLUDE_SLOT:
            # 채팅 메시지는 사용자가 직접 쓴 것이라 빼지 않는다 — 지시가 아니면 칸에 배정한다.
            return item.id.startswith(MESSAGE_ID_PREFIX)
        return catalog.get_slot(item.slot_id) is None

    retry_ids = [line_id for line_id in chunk if line_id not in by_id or invalid(by_id[line_id])]
    if retry_ids:
        logger.info("file_assign: 누락·잘못 배정된 줄 %d개 재요청", len(retry_ids))
        retry_chain = reassign_prompt | llm.with_structured_output(AssignOutput)
        retry: AssignOutput = await retry_chain.ainvoke(
            {"catalog": rendered_catalog, "document": document, "ids": ", ".join(retry_ids)}
        )
        for item in retry.items:
            if item.id in retry_ids and not invalid(item):
                by_id[item.id] = item
    return by_id


def _stitch_chunk_episodes(chunks: list[list[str]], by_id: dict[str, LineAssignment]) -> None:
    """청크 경계에서 잘린 에피소드를 앞 청크의 에피소드에 잇는다.

    다음 청크 첫 에피소드에 대표(SUMMARY) 줄이 없고 앞 청크 마지막 내용 줄과 같은
    section이면, 같은 에피소드가 경계에서 잘린 것이다.
    """
    summary_episodes = {
        item.episode
        for item in by_id.values()
        if item.slot_id.endswith(".SUMMARY") and item.episode
    }
    for previous, chunk in zip(chunks, chunks[1:], strict=False):
        last = next(
            (by_id[i] for i in reversed(previous) if i in by_id and "." in by_id[i].slot_id), None
        )
        first = next((by_id[i] for i in chunk if i in by_id and "." in by_id[i].slot_id), None)
        if not last or not first or not last.episode or not first.episode:
            continue
        if first.slot_id.split(".")[0] != last.slot_id.split(".")[0]:
            continue
        if first.episode in summary_episodes:
            continue
        for item in by_id.values():
            if item.episode == first.episode:
                item.episode = last.episode


def _single_summary(line_ids: list[str], by_id: dict[str, LineAssignment]) -> None:
    """에피소드마다 대표(SUMMARY) 줄을 첫 줄 하나만 남긴다.

    모델이 프로젝트 안의 소제목("협상 전략 (Leverage)")에도 SUMMARY를 붙여, 업무 제목
    블록에 소제목이 줄줄이 이어 붙었다(포트폴리오 PDF 로컬 재현). 나머지는 같은
    에피소드의 바로 다음 내용 줄 칸으로 옮긴다(없으면 바로 앞 줄 칸).
    """
    seen: set[str] = set()
    ordered = [i for i in line_ids if i in by_id]
    for position, line_id in enumerate(ordered):
        item = by_id[line_id]
        if not item.slot_id.endswith(".SUMMARY") or not item.episode:
            continue
        if item.episode not in seen:
            seen.add(item.episode)
            continue
        same = [
            by_id[i]
            for i in ordered[position + 1 :] + ordered[:position][::-1]
            if by_id[i].episode == item.episode
            and by_id[i].slot_id.split(".")[0] == item.slot_id.split(".")[0]
            and not by_id[i].slot_id.endswith(".SUMMARY")
        ]
        if same:
            item.slot_id = same[0].slot_id


def _attach_to_projects(
    lines: dict[str, str], line_ids: list[str], by_id: dict[str, LineAssignment]
) -> None:
    """프로젝트 제목이 둘 이상인 문서는 프로젝트 제목만 담당업무 대표 줄로 인정한다.

    포트폴리오에서 모델이 프로젝트 안의 "Key: 협상, 원가 절감", "[고객 등급 정의]"
    같은 줄에도 새 업무를 열었다(실제 포트폴리오 5건 중 4건). 프로젝트 밖 담당업무
    에피소드는 바로 앞 프로젝트의 에피소드로 옮기고, 대표 줄은 같은 에피소드 다음 줄의
    칸을 따른다.
    """
    ordered = [i for i in line_ids if i in by_id]
    project_episode: dict[int, str] = {}
    for line_id in ordered:
        item = by_id[line_id]
        number = project_number(lines[line_id])
        if number is not None and item.slot_id == "TASK.SUMMARY" and item.episode:
            project_episode.setdefault(number, item.episode)
    if len(project_episode) < 2:
        return

    projects = set(project_episode.values())
    current: str | None = None
    for position, line_id in enumerate(ordered):
        item = by_id[line_id]
        number = project_number(lines[line_id])
        if number is not None:
            current = project_episode.get(number, current)
            continue
        if current is None or not item.slot_id.startswith("TASK.") or item.episode in projects:
            continue
        # 프로젝트 밖 에피소드(대표 줄이 있든 없든)는 바로 앞 프로젝트에 붙인다.
        old_episode = item.episode
        if item.slot_id == "TASK.SUMMARY":
            item.slot_id = next(
                (
                    by_id[i].slot_id
                    for i in ordered[position + 1 :]
                    if by_id[i].episode == old_episode
                    and by_id[i].slot_id.startswith("TASK.")
                    and by_id[i].slot_id != "TASK.SUMMARY"
                ),
                "TASK.BASIC.EXECUTION",
            )
        for other in by_id.values():
            if old_episode and other.episode == old_episode:
                other.episode = current
        item.episode = current


def _demote_headings(lines: dict[str, str], by_id: dict[str, LineAssignment]) -> None:
    """구획 이름 줄과, 혼자뿐인 제목 모양 SUMMARY를 제목으로 돌린다."""
    episode_size: dict[str, int] = {}
    for item in by_id.values():
        if item.episode:
            episode_size[item.episode] = episode_size.get(item.episode, 0) + 1
    for line_id, item in by_id.items():
        text = lines[line_id]
        if is_section_name(text) and heading_like(text):
            item.slot_id = HEADING_SLOT
        elif (
            item.slot_id.endswith(".SUMMARY")
            and heading_like(text)
            and episode_size.get(item.episode or "", 0) <= 1
        ):
            item.slot_id = HEADING_SLOT


async def assign_lines(
    lines: list[str], catalog: TemplateCatalog, message_lines: list[str] | None = None
) -> FileAssignment:
    """파일 줄(과 함께 온 채팅 메시지 줄)을 칸에 배정한다. 문서 관문과 배정은 동시에 돈다.

    메시지 줄은 `m_N`, 파일 줄은 `it_N`으로 번호를 붙인다. 메시지 줄은 작업 지시면
    INSTRUCTION, 경험 내용이면 파일 줄과 같이 칸에 배정한다. 지시로 범위 밖이 된 파일
    줄은 "사용자요청" 사유로 제외 후보가 되고, 지시를 함께 보여주는 확인 호출을 거친다.

    Raises:
        Exception: LLM 호출 실패 — 호출하는 노드가 LlmError로 바꾼다
    """
    message_lines = message_lines or []
    message_map = {f"{MESSAGE_ID_PREFIX}{i}": text for i, text in enumerate(message_lines, 1)}
    file_map = {f"it_{index}": text for index, text in enumerate(lines, start=1)}
    line_map = {**message_map, **file_map}
    file_ids = list(file_map)
    line_ids = list(line_map)
    llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)

    scope: dict = {}
    chunks = _chunks(file_ids, file_map) if file_ids else [[]]
    chunks[0] = list(message_map) + chunks[0]
    jobs = []
    for index, chunk in enumerate(chunks):
        file_part = [i for i in chunk if i in file_map]
        start = file_ids.index(file_part[0]) if file_part else 0
        context = [file_map[i] for i in file_ids[:start] if heading_like(file_map[i])][-3:]
        message_context = [] if index == 0 else list(message_map.values())
        jobs.append(_assign_chunk(llm, catalog, line_map, chunk, context, message_context, scope))
    gate_chain = document_gate_prompt | llm.with_structured_output(DocumentGate)
    # 관문과 배정을 동시에 시작하되 관문 결과를 먼저 기다린다. 경험 문서가 아니면
    # 아직 도는 배정 호출을 취소해 바로 끝낸다 — 6쪽 문제지에서 관문은 2초 안에
    # 판정했는데 배정을 다 기다리느라 14초가 걸렸다(로컬 측정).
    assign_tasks = [asyncio.create_task(job) for job in jobs]
    try:
        gate: DocumentGate = await gate_chain.ainvoke(
            {"document": "\n".join(lines)[:GATE_MAX_CHARS]}
        )
    except BaseException:
        for task in assign_tasks:
            task.cancel()
        raise
    logger.info(
        "file_assign: 문서 관문 %s (%s) — %s", gate.is_experience, gate.doc_type, gate.reason
    )
    if gate.is_experience and not _grounded(gate.evidence, lines):
        # "중간고사 대비 문제집이니 학습 경험"처럼 통과시킨 적이 있다(실제 문제지 재현).
        # 작성자가 한 일을 쓴 문장을 원문에서 인용하지 못하면 경험 문서가 아니다.
        logger.info("file_assign: 문서 관문 근거 문장 없음 %s", gate.evidence[:3])
        gate.is_experience = False
    if not gate.is_experience:
        for task in assign_tasks:
            task.cancel()
        await asyncio.gather(*assign_tasks, return_exceptions=True)
        return FileAssignment(is_experience=False, doc_type=gate.doc_type, lines=line_map)
    chunk_results = await asyncio.gather(*assign_tasks)

    by_id: dict[str, LineAssignment] = {}
    for result in chunk_results:
        by_id.update(result)
    _stitch_chunk_episodes(chunks, by_id)
    _single_summary(line_ids, by_id)
    _attach_to_projects(line_map, line_ids, by_id)

    instructions = [line_map[i] for i, item in by_id.items() if item.slot_id == INSTRUCTION_SLOT]
    if all(_is_plain_request(text) for text in instructions):
        # "정리해줘"뿐이면 범위 지시가 없다. 모델이 '사용자요청'으로 뺀 줄은 무관 확인으로 돌린다.
        instructions = []
        scope = {}
    excluded, requested = await _verify_exclusions(
        llm, catalog, line_map, line_ids, by_id, instructions
    )
    _demote_headings(line_map, by_id)
    requested = requested + _scope_excluded(by_id, file_ids, scope, set(requested))

    dropped = set(excluded) | set(requested)
    assignments: dict[str, dict] = {}
    inherited = 0
    previous: dict | None = None
    for line_id in line_ids:
        item = by_id.get(line_id)
        if line_id in dropped:
            continue
        if item is not None and item.slot_id == INSTRUCTION_SLOT:
            continue
        if item is not None and item.slot_id == HEADING_SLOT and heading_like(line_map[line_id]):
            continue
        if item is not None and catalog.get_slot(item.slot_id) is not None:
            previous = {"slot_id": item.slot_id, "episode": item.episode}
            assignments[line_id] = previous
        elif previous is not None:
            # 재요청까지 실패한 내용 줄은 버리지 않고 바로 앞 줄의 칸에 붙인다.
            assignments[line_id] = dict(previous)
            inherited += 1
    if inherited:
        logger.warning("file_assign: 배정 못 받은 내용 줄 %d개를 앞 줄 칸에 붙였습니다", inherited)
    return FileAssignment(
        is_experience=True,
        doc_type=gate.doc_type,
        lines=line_map,
        assignments=assignments,
        excluded=[line_map[i] for i in excluded],
        requested_excluded=[line_map[i] for i in requested],
    )


_PLAIN_REQUEST = re.compile(
    r"^[\s.!~]*(?:이\s*)?(?:경험|내용|파일)?\s*(?:을|를)?\s*정리\s*(?:해\s*(?:줘|주세요|주라)|부탁(?:해|드려요)?)[\s.!~]*$"
)


def _is_plain_request(text: str) -> bool:
    """범위 없는 단순 정리 요청("정리해줘", "이 파일 정리해 주세요")인지 본다."""
    return bool(_PLAIN_REQUEST.match(re.sub(r"^(?:이|아래|첨부한?|올린)\s*", "", text.strip())))


def _scope_excluded(
    by_id: dict[str, LineAssignment], file_ids: list[str], scope: dict, already: set[str]
) -> list[str]:
    """메시지의 구획 단위 지시("문제해결만", "배운 점은 빼고")를 코드로 적용한다.

    대부분의 줄을 빼야 하는 "~만" 지시는 모델이 줄마다 EXCLUDE를 붙이지 못했다(로컬
    재현). 모델은 구획 목록만 답하고, 실제 제외는 칸 배정 결과로 코드가 한다. 파일
    줄에만 적용한다 — 사용자가 채팅으로 쓴 내용은 빼지 않는다.
    """
    only = set(scope.get("only", set()))
    excluded = set(scope.get("excluded", set()))
    if not only and not excluded:
        return []
    out: list[str] = []
    for line_id in file_ids:
        item = by_id.get(line_id)
        if item is None or line_id in already or "." not in item.slot_id:
            continue
        section = item.slot_id.split(".")[0]
        if (only and section not in only) or section in excluded:
            out.append(line_id)
    if out:
        logger.info(
            "file_assign: 구획 지시(only=%s, excluded=%s)로 %d줄 제외", only, excluded, len(out)
        )
    return out


async def _verify_exclusions(
    llm,
    catalog: TemplateCatalog,
    lines: dict[str, str],
    line_ids: list[str],
    by_id: dict[str, LineAssignment],
    instructions: list[str],
) -> tuple[list[str], list[str]]:
    """제외 후보를 한 번 더 확인한다. 남기기로 한 줄은 다시 배정한다.

    무관해서 뺀 후보는 "경험 서술이 조금이라도 있으면 남긴다"로, 사용자 지시로 뺀 후보는
    지시를 함께 보여주고 "지시상 빼야 하는 줄인가"로 확인한다 — 지시로 빼는 줄은 경험
    내용이 있어도 빼는 게 맞기 때문이다. 두 확인은 동시에 돈다.

    메시지에 지시가 있으면 무관 후보도 지시 확인을 함께 받는다. 다른 경험은 무관 확인에서
    남기는데, 모델이 "빼줘"로 뺀 줄에 이유를 "다른경험"처럼 달면 지시가 있어도 남았다
    (로컬 재현, 5번 중 2번).

    Returns:
        (무관해서 제외 확정된 줄 id, 사용자 지시로 제외 확정된 줄 id)
    """
    candidates = [line_id for line_id, item in by_id.items() if item.slot_id == EXCLUDE_SLOT]
    if not candidates:
        return [], []
    requested_ids = [
        i
        for i in candidates
        if instructions and REQUESTED_EXCLUDE_REASON in (by_id[i].reason or "")
    ]
    unrelated_ids = [i for i in candidates if i not in requested_ids]
    document = _render(lines, line_ids)

    async def verify(prompt, ids: list[str], extra: dict) -> set[str]:
        if not ids:
            return set()
        chain = prompt | llm.with_structured_output(_VerifyOutput)
        verdicts: _VerifyOutput = await chain.ainvoke(
            {"document": document, "candidates": _render(lines, ids), **extra}
        )
        # 모델이 후보가 아닌 줄까지 답하는 경우가 있다. 후보 밖 답이 다른 확인 결과를
        # 덮어쓰지 않게 후보 id만 본다.
        verdict_by_id = {v.id: v.keep for v in verdicts.items if v.id in ids}
        return {i for i in ids if verdict_by_id.get(i, True)}

    unrelated_check_ids = unrelated_ids if instructions else []
    keep_unrelated, keep_requested = await asyncio.gather(
        verify(exclude_verify_prompt, unrelated_ids, {}),
        verify(
            requested_exclude_verify_prompt,
            requested_ids + unrelated_check_ids,
            {"instructions": "\n".join(instructions)},
        ),
    )
    # 무관 후보는 무관 확인이 남기라고 했어도 지시 확인이 빼라고 하면 사용자 요청으로 뺀다.
    by_request = [i for i in unrelated_check_ids if i in keep_unrelated and i not in keep_requested]
    requested_ids += by_request
    unrelated_ids = [i for i in unrelated_ids if i not in by_request]
    keep = (keep_unrelated - set(by_request)) | (keep_requested & set(requested_ids))
    if keep:
        retry_chain = reassign_prompt | llm.with_structured_output(AssignOutput)
        retry: AssignOutput = await retry_chain.ainvoke(
            {
                "catalog": render_catalog(catalog),
                "document": document,
                "ids": ", ".join(sorted(keep)),
            }
        )
        for item in retry.items:
            if (
                item.id in keep
                and item.slot_id not in (HEADING_SLOT, EXCLUDE_SLOT, INSTRUCTION_SLOT)
                and catalog.get_slot(item.slot_id) is not None
            ):
                by_id[item.id] = item
    confirmed_unrelated = [i for i in unrelated_ids if i not in keep]
    confirmed_requested = [i for i in requested_ids if i not in keep]
    for line_id in confirmed_unrelated + confirmed_requested:
        by_id[line_id].slot_id = EXCLUDE_SLOT
    logger.info(
        "file_assign: 제외 후보 %d개 중 무관 %d개·사용자 요청 %d개 제외 확정",
        len(candidates),
        len(confirmed_unrelated),
        len(confirmed_requested),
    )
    return confirmed_unrelated, confirmed_requested
