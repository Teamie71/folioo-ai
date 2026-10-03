"""줄별 배정(slot·에피소드)으로 블록 operation을 만든다. LLM을 부르지 않는다.

구획 → 활동의 기존 카테고리 → 에피소드(앵커) → 칸 순서로 놓는다. 메인 서버가 새
활동에 미리 깔아 둔 빈 블록은 새로 만들지 않고 그 블록을 update로 채운다.
"""

import re
from collections import Counter

from features.experience_map.config import MAX_CONTENT_LENGTH
from features.experience_map.file_pipeline.split import (
    is_subheading,
    project_number,
    strip_markers,
)
from features.experience_map.nodes.refine import _bigram_overlap_ratio
from features.experience_map.nodes.structure import (
    _EMPTY_SLOT_GUIDE_RE,
    _parse_tree_lines,
    _placeholder_to_slot_map,
    _subtree_known_slots_with_parent,
)
from features.experience_map.schemas import SECTION_LABELS
from features.experience_map.state import ExperienceMapState
from features.experience_map.templates import TemplateCatalog

_SECTION_BY_TITLE = {label.replace(" ", ""): section for section, label in SECTION_LABELS.items()}
_SECTION_BY_TITLE.update({"성과": "ACHIEVEMENT", "문제해결": "PROBLEM_SOLVING"})


DUPLICATE_OVERLAP = 0.75
_LEADING_LABEL = re.compile(r"^\s*(?:[-•·*▪◦▶]\s*|\d+[.)]\s*)?(?:[^:：]{1,12}[:：]\s*)?")
"""원문 글자쌍이 기존 블록에 이 비율 이상 남아 있으면 같은 내용으로 보고 다시 넣지 않는다."""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _join(texts: list[str]) -> list[str]:
    """같은 칸에 간 줄을 원문 순서대로 한 문단으로 합친다. 넘치면 다음 블록으로 넘긴다.

    줄바꿈으로 이으면 문장 다듬기가 적용되지 않은 블록에 목록처럼 끊긴 줄이 그대로
    보였다(dev 제보). 띄어쓰기로 잇고, 소제목 줄은 바로 다음 내용 앞에 "소제목: 내용"
    으로 붙인다.
    """
    merged: list[str] = []
    for text in texts:
        previous = merged[-1] if merged else ""
        if previous and is_subheading(previous) and not is_subheading(text):
            separator = " " if previous.rstrip().endswith((":", "：")) else ": "
            merged[-1] = f"{previous.rstrip()}{separator}{text}"
        else:
            merged.append(text)
    blocks: list[str] = []
    for text in merged:
        if blocks and len(blocks[-1]) + 1 + len(text) <= MAX_CONTENT_LENGTH:
            blocks[-1] = f"{blocks[-1]} {text}"
        else:
            blocks.append(text[:MAX_CONTENT_LENGTH])
    return blocks


class _TreeBuilder:
    def __init__(self, state: ExperienceMapState, catalog: TemplateCatalog) -> None:
        self.state = state
        self.catalog = catalog
        self.target = state["target_experience_alias"]
        self.metadata = state.get("alias_metadata", {})
        self.tree = _parse_tree_lines(state.get("activity_tree_text") or "")
        self.labels = {alias: label for _, alias, label in self.tree}
        self.placeholder_to_slot = _placeholder_to_slot_map(catalog)
        self.anchor_slots = {
            section.section_id: slot.slot_id
            for section in catalog.sections
            for slot in section.slots
            if slot.is_anchor
        }
        self.templates = {
            f"{section.section_id}.{template.template_id}": [
                slot.slot_id for slot in template.slots
            ]
            for section in catalog.sections
            for template in section.templates
        }
        self.containers = self._existing_containers()
        self.new_containers: dict[str, str] = {}
        self.used_targets: set[str] = set()
        self.items: list[dict] = []
        self.counter = 0

    def _existing_containers(self) -> dict[str, str]:
        """활동 바로 아래 기존 카테고리 별칭. kind가 없으면 제목으로 알아본다."""
        containers: dict[str, str] = {}
        for alias, block in self.metadata.items():
            if block.get("parent_alias") != self.target:
                continue
            kind = str(block.get("kind") or "")
            section = kind.removeprefix("SECTION_") if kind.startswith("SECTION_") else None
            if section is None:
                title = self.labels.get(alias, "").replace(" ", "")
                section = _SECTION_BY_TITLE.get(title)
            if section:
                containers.setdefault(section, alias)
        return containers

    def _next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"file_{prefix}_{self.counter}"

    def _is_empty(self, alias: str) -> bool:
        return bool(_EMPTY_SLOT_GUIDE_RE.match(self.labels.get(alias, "")))

    def _child_slots(self, alias: str) -> dict[str, str]:
        return {
            slot_id: child
            for slot_id, child, parent in _subtree_known_slots_with_parent(
                self.tree, alias, self.placeholder_to_slot
            )
            if parent == alias
        }

    def _parent_of_section(self, section: str) -> tuple[dict, str | None]:
        """카테고리 부모 참조와 기존 별칭. 없으면 새 카테고리를 한 번만 만든다."""
        if section in self.containers:
            alias = self.containers[section]
            return {"parent_ref": alias}, alias
        if section not in self.new_containers:
            item_id = self._next_id("category")
            self.items.append(
                {
                    "item_id": item_id,
                    "action": "add",
                    "parent_ref": self.target,
                    "section_kind": section,
                }
            )
            self.new_containers[section] = item_id
        return {"parent_item_id": self.new_containers[section]}, None

    def _place(self, parent: dict, slot_id: str, texts: list[str], existing: dict[str, str]):
        """칸 하나를 채운다. 기존 빈 블록이 있으면 update, 아니면 add."""
        for index, text in enumerate(_join(texts)):
            target = existing.get(slot_id) if index == 0 else None
            if target and target not in self.used_targets and self._is_empty(target):
                self.used_targets.add(target)
                self.items.append(
                    {
                        "item_id": self._next_id("update"),
                        "action": "update",
                        "target_ref": target,
                        "text": text,
                    }
                )
            else:
                self.items.append(
                    {
                        "item_id": self._next_id("block"),
                        "action": "add",
                        **parent,
                        "slot_id": slot_id,
                        "text": text,
                    }
                )

    def _blank_anchor(self, container: str, anchor_slot: str, slots: set[str]) -> str | None:
        """같은 템플릿 칸을 모두 가진, 아직 안 쓴 미리 깔린 빈 앵커."""
        for slot_id, alias, parent in _subtree_known_slots_with_parent(
            self.tree, container, self.placeholder_to_slot
        ):
            if parent != container or slot_id != anchor_slot or alias in self.used_targets:
                continue
            if not self._is_empty(alias):
                continue
            children = self._child_slots(alias)
            if slots <= set(children) and all(self._is_empty(c) for c in children.values()):
                return alias
        return None

    def _normalize_template(
        self, slots: dict[str, list[str]], anchor_slot: str
    ) -> tuple[dict[str, list[str]], list[str]]:
        """한 에피소드는 가장 많이 쓴 템플릿 하나로 통일한다."""
        child_slots = [slot for slot in slots if slot != anchor_slot]
        counts = Counter(".".join(slot.split(".")[:2]) for slot in child_slots)
        chosen = counts.most_common(1)[0][0] if counts else None
        template_slots = self.templates.get(chosen or "", [])
        normalized: dict[str, list[str]] = {}
        for slot_id, texts in slots.items():
            if slot_id != anchor_slot and chosen and not slot_id.startswith(f"{chosen}."):
                suffix = slot_id.split(".")[-1]
                slot_id = next(
                    (s for s in template_slots if s.endswith(f".{suffix}")), template_slots[0]
                )
            normalized.setdefault(slot_id, []).extend(texts)
        return normalized, template_slots

    def _place_episode(self, section: str, slots: dict[str, list[str]]) -> None:
        anchor_slot = self.anchor_slots[section]
        normalized, template_slots = self._normalize_template(slots, anchor_slot)
        anchor_texts = normalized.pop(anchor_slot, [])
        if not anchor_texts and normalized:
            # 대표 줄이 없으면 첫 내용 줄을 앵커로 올린다.
            first_slot = next(iter(normalized))
            anchor_texts = [normalized[first_slot].pop(0)]
            if not normalized[first_slot]:
                normalized.pop(first_slot)
        anchor_text = " ".join(anchor_texts)[:MAX_CONTENT_LENGTH]

        parent, container = self._parent_of_section(section)
        blank = self._blank_anchor(container, anchor_slot, set(normalized)) if container else None
        if blank:
            self.used_targets.add(blank)
            self.items.append(
                {
                    "item_id": self._next_id("update"),
                    "action": "update",
                    "target_ref": blank,
                    "text": anchor_text,
                }
            )
            children = self._child_slots(blank)
            for slot_id, texts in normalized.items():
                self._place({"parent_ref": blank}, slot_id, texts, children)
            return

        anchor_id = self._next_id("anchor")
        self.items.append(
            {
                "item_id": anchor_id,
                "action": "add",
                **parent,
                "slot_id": anchor_slot,
                "text": anchor_text,
            }
        )
        for slot_id in template_slots or list(normalized):
            texts = normalized.get(slot_id)
            if texts:
                self._place({"parent_item_id": anchor_id}, slot_id, texts, {})
            else:
                # 새 앵커는 템플릿 칸을 모두 펼친다(빈 칸은 가이드 문구로 보인다).
                self.items.append(
                    {
                        "item_id": self._next_id("slot"),
                        "action": "add",
                        "parent_item_id": anchor_id,
                        "slot_id": slot_id,
                        "text": None,
                    }
                )
        for slot_id, texts in normalized.items():
            if template_slots and slot_id not in template_slots:
                self._place({"parent_item_id": anchor_id}, slot_id, texts, {})

    def _existing_texts(self) -> set[str]:
        """활동에 이미 들어 있는 블록 내용(공백 무시). 같은 파일을 다시 올려도 중복으로 넣지 않는다."""
        return {
            _normalize(label)
            for _, _, label in self.tree
            if label and not _EMPTY_SLOT_GUIDE_RE.match(label)
        }

    @staticmethod
    def _duplicate(text: str, existing: set[str]) -> bool:
        """이미 활동에 있는 블록과 같은 내용인지 본다.

        활동의 블록은 정제(문장 다듬기)를 거친 문장이라 원문과 글자가 다르다. 같은 파일을
        다시 올렸을 때 정확히 같은지만 보면 하나도 못 걸렀다(로컬 재현) — 원문 글자쌍이
        기존 블록에 75% 이상 남아 있으면 같은 내용으로 본다.
        """
        if _normalize(text) in existing:
            return True
        # 정제는 불릿과 "성장한 부분:" 같은 앞 라벨을 떼므로 비교 전에 같이 뗀다.
        body = _LEADING_LABEL.sub("", text)
        return len(_normalize(body)) >= 8 and any(
            _bigram_overlap_ratio(body, block) >= DUPLICATE_OVERLAP for block in existing
        )

    def build(self, lines: dict[str, str], assignments: dict[str, dict]) -> list[dict]:
        flat: dict[str, dict[str, list[str]]] = {}
        episodes: dict[tuple[str, str], dict[str, list[str]]] = {}
        pending: dict[tuple[str, str], list[str]] = {}
        existing = self._existing_texts()
        skipped = 0
        for line_id, text in lines.items():
            assignment = assignments.get(line_id)
            if assignment is None:
                continue
            if self._duplicate(text, existing):
                skipped += 1
                continue
            text = strip_markers(text)
            if not text:
                continue
            slot_id = assignment["slot_id"]
            section = slot_id.split(".")[0]
            if section in self.anchor_slots:
                episode = assignment.get("episode") or f"auto_{section}"
                # 포트폴리오는 목차("P2. 굿즈 공동구매")와 본문("Project 2. 학과 굿즈
                # 공동구매"), 페이지마다 반복되는 머리글로 같은 프로젝트를 여러 번 적는다.
                # 모델이 그때마다 새 에피소드를 열어(로컬 재현) 첫 줄이 같거나 프로젝트
                # 번호가 같은 에피소드는 합친다.
                head = lines.get(episode, episode)
                number = project_number(head)
                key = (section, f"project:{number}" if number is not None else _normalize(head))
                group = episodes.setdefault(key, {})
                anchor_slot = self.anchor_slots[section]
                if (
                    slot_id == anchor_slot
                    and group.get(anchor_slot)
                    and project_number(text) is None
                ):
                    # 합쳐진 에피소드의 대표 줄은 소제목이다. 바로 다음 줄 칸으로 보낸다.
                    pending.setdefault(key, []).append(text)
                    continue
                if slot_id != anchor_slot and pending.get(key):
                    group.setdefault(slot_id, []).extend(pending.pop(key))
                texts = group.setdefault(slot_id, [])
                if text not in texts:
                    texts.append(text)
            else:
                flat.setdefault(section, {}).setdefault(slot_id, []).append(text)

        for section, slots in flat.items():
            parent, container = self._parent_of_section(section)
            existing = self._child_slots(container) if container else {}
            for slot_id, texts in slots.items():
                self._place(parent, slot_id, texts, existing)
        for key, texts in pending.items():
            # 뒤에 내용 줄이 없던 소제목은 대표 칸에 남긴다(원문을 버리지 않는다).
            episodes[key].setdefault(self.anchor_slots[key[0]], []).extend(texts)
        for (section, _episode), slots in episodes.items():
            anchor_slot = self.anchor_slots[section]
            titles = [t for t in slots.get(anchor_slot, []) if project_number(t) is not None]
            if len(titles) > 1:
                # 같은 프로젝트 제목이 여러 번이면 가장 자세한 하나만 앵커로 쓴다.
                keep = max(titles, key=len)
                slots[anchor_slot] = [
                    t for t in slots[anchor_slot] if t == keep or project_number(t) is None
                ]
            self._place_episode(section, slots)
        self.skipped_duplicates = skipped
        return self.items


def build_file_items(
    lines: dict[str, str],
    assignments: dict[str, dict],
    state: ExperienceMapState,
    catalog: TemplateCatalog,
) -> list[dict]:
    """파일 줄 배정을 `structured_items`(StructuredItem dict 목록)로 바꾼다."""
    return _TreeBuilder(state, catalog).build(lines, assignments)
