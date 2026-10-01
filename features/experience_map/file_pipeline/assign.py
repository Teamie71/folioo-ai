"""파일 줄을 템플릿 칸에 배정한다: 문서 관문 ∥ 칸 배정 → 제외 확인 → 정리.

LLM은 줄 번호별로 slot·에피소드만 고르고 원문은 다시 쓰지 않는다. 줄을 버리는 건
(1) 코드가 확실한 노이즈로 판단했거나, (2) LLM이 제목이라 하고 코드 기준으로도 제목
모양이거나, (3) LLM이 무관하다고 하고 확인 호출에서도 무관하다고 한 경우뿐이다 —
LLM에게 버리는 권한을 그대로 주면 실제 상세정보를 통째로 버린 적이 있다.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from common.llm import get_experience_map_llm
from features.experience_map.config import get_settings
from features.experience_map.file_pipeline.split import heading_like, is_section_name
from features.experience_map.prompts.file_assign import (
    EXCLUDE_SLOT,
    HEADING_SLOT,
    assign_prompt,
    document_gate_prompt,
    exclude_verify_prompt,
    reassign_prompt,
)
from features.experience_map.prompts.structure import render_catalog
from features.experience_map.templates import TemplateCatalog

logger = logging.getLogger(__name__)

CHUNK_LINES = 40
"""한 번의 배정 호출에 맡기는 줄 수. 넘으면 제목 경계에서 나눠 병렬로 부른다."""

GATE_MAX_CHARS = 12_000
"""문서 관문에 보내는 원문 길이 상한. 경험 문서인지 판단하는 데는 앞부분으로 충분하다."""


class LineAssignment(BaseModel):
    id: str
    slot_id: str = Field(
        ..., description="카탈로그 slot_id, 문서 제목이면 HEADING, 이 경험과 무관하면 EXCLUDE"
    )
    episode: str | None = Field(
        None, description="담당업무·문제해결이면 그 업무/에피소드의 첫 줄 id"
    )
    reason: str | None = Field(
        None, description="EXCLUDE일 때만: 다른경험/자기소개/개인정보/문서메타/무관"
    )


class AssignOutput(BaseModel):
    items: list[LineAssignment]


class DocumentGate(BaseModel):
    is_experience: bool = Field(
        ..., description="작성자 본인이 직접 수행한 활동·프로젝트·업무·학습 경험을 서술하면 true"
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
    """배정 결과. `assignments`는 반영할 줄만, `excluded`는 무관해서 뺀 줄."""

    is_experience: bool
    doc_type: str
    lines: dict[str, str]
    assignments: dict[str, dict] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)


def _chunks(line_ids: list[str], lines: dict[str, str]) -> list[list[str]]:
    """긴 문서를 제목 줄 경계에서 약 CHUNK_LINES줄씩 자른다."""
    if len(line_ids) <= CHUNK_LINES:
        return [line_ids]
    chunks: list[list[str]] = []
    current: list[str] = []
    for line_id in line_ids:
        at_heading = heading_like(lines[line_id])
        if (len(current) >= CHUNK_LINES * 0.6 and at_heading) or len(current) >= CHUNK_LINES * 1.5:
            chunks.append(current)
            current = []
        current.append(line_id)
    if current:
        chunks.append(current)
    return chunks


def _render(lines: dict[str, str], line_ids: list[str]) -> str:
    return "\n".join(f"[{line_id}] {lines[line_id]}" for line_id in line_ids)


async def _assign_chunk(
    llm, catalog: TemplateCatalog, lines: dict[str, str], chunk: list[str], context: list[str]
) -> dict[str, LineAssignment]:
    """한 청크를 배정하고, 빠지거나 잘못 배정된 줄만 한 번 더 요청한다."""
    rendered_catalog = render_catalog(catalog)
    document = "".join(f"(앞 문맥 제목) {heading}\n" for heading in context) + _render(lines, chunk)
    chain = assign_prompt | llm.with_structured_output(AssignOutput)
    output: AssignOutput = await chain.ainvoke({"catalog": rendered_catalog, "document": document})
    chunk_ids = set(chunk)
    by_id = {item.id: item for item in output.items if item.id in chunk_ids}

    def invalid(item: LineAssignment) -> bool:
        if item.slot_id == HEADING_SLOT:
            return not heading_like(lines[item.id])
        if item.slot_id == EXCLUDE_SLOT:
            return False
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


async def assign_lines(lines: list[str], catalog: TemplateCatalog) -> FileAssignment:
    """줄 목록을 칸에 배정한다. 문서 관문과 배정은 동시에 돈다.

    Raises:
        Exception: LLM 호출 실패 — 호출하는 노드가 LlmError로 바꾼다
    """
    line_map = {f"it_{index}": text for index, text in enumerate(lines, start=1)}
    line_ids = list(line_map)
    llm = get_experience_map_llm(timeout=get_settings().timeouts.llm)

    chunks = _chunks(line_ids, line_map)
    jobs = []
    for chunk in chunks:
        start = line_ids.index(chunk[0])
        context = [line_map[i] for i in line_ids[:start] if heading_like(line_map[i])][-3:]
        jobs.append(_assign_chunk(llm, catalog, line_map, chunk, context))
    gate_chain = document_gate_prompt | llm.with_structured_output(DocumentGate)
    gate_job = gate_chain.ainvoke({"document": "\n".join(lines)[:GATE_MAX_CHARS]})
    gate, *chunk_results = await asyncio.gather(gate_job, *jobs)
    logger.info(
        "file_assign: 문서 관문 %s (%s) — %s", gate.is_experience, gate.doc_type, gate.reason
    )
    if not gate.is_experience:
        return FileAssignment(is_experience=False, doc_type=gate.doc_type, lines=line_map)

    by_id: dict[str, LineAssignment] = {}
    for result in chunk_results:
        by_id.update(result)
    _stitch_chunk_episodes(chunks, by_id)

    excluded = await _verify_exclusions(llm, catalog, line_map, line_ids, by_id)
    _demote_headings(line_map, by_id)

    excluded_set = set(excluded)
    assignments: dict[str, dict] = {}
    inherited = 0
    previous: dict | None = None
    for line_id in line_ids:
        item = by_id.get(line_id)
        if item is not None and item.slot_id == HEADING_SLOT and heading_like(line_map[line_id]):
            continue
        if line_id in excluded_set:
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
    )


async def _verify_exclusions(
    llm,
    catalog: TemplateCatalog,
    lines: dict[str, str],
    line_ids: list[str],
    by_id: dict[str, LineAssignment],
) -> list[str]:
    """제외 후보를 한 번 더 확인한다. 남기기로 한 줄은 다시 배정한다."""
    candidates = [line_id for line_id, item in by_id.items() if item.slot_id == EXCLUDE_SLOT]
    if not candidates:
        return []
    document = _render(lines, line_ids)
    verify_chain = exclude_verify_prompt | llm.with_structured_output(_VerifyOutput)
    verdicts: _VerifyOutput = await verify_chain.ainvoke(
        {"document": document, "candidates": _render(lines, candidates)}
    )
    answered = {verdict.id for verdict in verdicts.items}
    keep = {verdict.id for verdict in verdicts.items if verdict.keep} | {
        line_id for line_id in candidates if line_id not in answered
    }
    confirmed = [line_id for line_id in candidates if line_id not in keep]
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
                and item.slot_id not in (HEADING_SLOT, EXCLUDE_SLOT)
                and catalog.get_slot(item.slot_id) is not None
            ):
                by_id[item.id] = item
    for line_id in confirmed:
        by_id[line_id].slot_id = EXCLUDE_SLOT
    logger.info("file_assign: 제외 후보 %d개 중 %d개 제외 확정", len(candidates), len(confirmed))
    return confirmed
