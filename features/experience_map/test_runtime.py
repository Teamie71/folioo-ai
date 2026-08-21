"""경험정리 수동 테스트 UI 전용 in-memory 맵과 커밋 실행기.

메인 서버의 ``block`` DDL·커밋 API가 없는 로컬 환경에서만 실제 LLM 노드의
블록 수정 흐름을 점검한다. ``EXPERIENCE_MAP_TEST_UI_ENABLED``일 때에만 앱
lifespan에서 주입하며, 운영 Repository·메인 서버 쓰기를 대체하지 않는다.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.schemas.experience_map import CompletedMessage, MessageCompleteEvent
from features.experience_map.coordinator import coordinate
from features.experience_map.graph import build_graph
from features.experience_map.graph_runner import CheckpointGraphRunner, GraphRunner
from features.experience_map.map_context import (
    ExperienceMapSnapshot,
    MapBlockRow,
    build_map_snapshot,
)
from features.experience_map.nodes.fallback import fallback_message
from features.experience_map.schemas import AppliedItem, CommitResult, StructuredItem
from features.experience_map.state import ExperienceMapState
from features.experience_map.templates import TemplateCatalogClient


def _initial_rows() -> list[MapBlockRow]:
    """블록 수정 테스트에 쓰는 결정적 샘플 맵을 만든다."""

    def row(
        block_id: str,
        parent_id: str | None,
        level: int,
        position: int,
        content: str | None,
    ) -> MapBlockRow:
        return MapBlockRow(
            block_id=block_id,
            parent_id=parent_id,
            level=level,
            kind="CONTENT",
            position=position,
            content=content,
            placeholder=None,
            is_text_editable=True,
            is_deletable=False,
        )

    return [
        row("100", None, 1, 1, "프로젝트 경험"),
        row("200", "100", 2, 1, "교내 커머스 리뉴얼"),
        row("300", "200", 3, 1, "문제 해결"),
        row("301", "300", 4, 1, "행사 신청 페이지의 이탈률이 높았다."),
        row("302", "300", 4, 2, "GA4 퍼널 분석 후 입력 단계를 5개에서 3개로 줄였다."),
        row("400", "200", 3, 2, "성과"),
        row("401", "400", 4, 1, "신청 전환율과 완료율을 개선했다."),
    ]


def _slot(slot_id: str, level: int, placeholder: str, example: str) -> dict[str, Any]:
    return {"slot_id": slot_id, "level": level, "placeholder": placeholder, "example": example}


def _template(template_id: str, label: str, slots: list[dict[str, Any]]) -> dict[str, Any]:
    return {"template_id": template_id, "label": label, "slots": slots}


# 에이전트 문서 3-0~3-4 의 확정 카탈로그(38개)를 그대로 옮긴다. level 4 슬롯 10개
# + level 5 하위 템플릿 28개(담당업무 1종×4 + 문제해결 6종×4). 예전엔 2개짜리
# 가짜 카탈로그를 썼는데, 그러면 담당업무·문제해결의 실제 템플릿이 하나도 없어
# 테스트 콘솔에서 어떤 입력을 넣어도 세부 슬롯이 적용된 적이 없었다.
_TASK_BASIC = _template(
    "BASIC",
    "기본",
    [
        _slot(
            "TASK.BASIC.PURPOSE",
            5,
            "이 업무를 진행한 목적은 무엇이며, 구체적으로 어떤 목표를 달성하고자 했나요?",
            "신규 브랜드 인지도를 확대하고, 2030 타겟 고객의 공식 SNS 채널 팔로워 1만 명 확보를 목표로 설정",
        ),
        _slot(
            "TASK.BASIC.RESEARCH",
            5,
            "원활한 업무 수행을 위해 조사한 정보나 추가로 학습한 내용은 무엇인가요?",
            "최근 소셜 미디어 알고리즘 변화와 타겟층이 선호하는 숏폼 영상 트렌드, 타사의 바이럴 성공 사례를 집중적으로 조사",
        ),
        _slot(
            "TASK.BASIC.EXECUTION",
            5,
            "실제 작업은 어떤 방식으로, 어떤 과정을 거쳐서 진행했나요?",
            "브랜드 핵심 메시지를 15초 이내로 압축한 숏폼 시리즈를 제작하고, A/B 테스트를 통해 반응률이 높은 소재에 광고 예산을 집중하는 방식으로 운영",
        ),
        _slot(
            "TASK.BASIC.RESULT",
            5,
            "업무 완료 후 나타난 결과는 무엇이며, 이 과정을 통해 배운 점은 무엇인가요?",
            "캠페인 한 달 만에 목표 팔로워 1만 명을 조기 달성했으며, 영상 도입부 3초의 시각적 요소가 사용자 체류 시간과 전환에 미치는 결정적인 영향을 체득",
        ),
    ],
)

_PROBLEM_SOLVING_TEMPLATES = [
    _template(
        "BASIC",
        "기본",
        [
            _slot(
                "PROBLEM_SOLVING.BASIC.PROBLEM",
                5,
                "어떤 문제가 발생했으며, 이를 해결해야 했던 이유는 무엇인가요?",
                "신규 프로모션 페이지 이탈률 70% 초과, 목표 가입자 수 달성을 위해 전환율 개선 필요",
            ),
            _slot(
                "PROBLEM_SOLVING.BASIC.CAUSE",
                5,
                "문제의 원인은 무엇이었고, 어떤 방식으로 원인을 파악했나요?",
                "GA4 퍼널 분석으로 사용자 이탈 구간을 추적하여, 모호한 CTA 카피와 복잡한 혜택 설명이 가입 단계의 병목 원인임을 확인",
            ),
            _slot(
                "PROBLEM_SOLVING.BASIC.SOLUTION",
                5,
                "해결책을 도출한 과정과 구체적인 실행 방법은 무엇인가요?",
                "핵심 혜택을 직관적으로 강조한 3가지 카피로 A/B 테스트 기획, 일정 기간 노출하여 클릭률 변화를 추적",
            ),
            _slot(
                "PROBLEM_SOLVING.BASIC.RESULT",
                5,
                "해결책 적용 후 나타난 결과와 그 검증 방법, 그리고 이 과정을 통해 배운 점은 무엇인가요?",
                "개선안 적용 후 가입 전환율 15% 상승, 타깃 니즈에 맞춘 직관적인 메시징과 데이터 기반 가설 검증의 중요성을 체득",
            ),
        ],
    ),
    _template(
        "INTERPERSONAL",
        "대인관계",
        [
            _slot(
                "PROBLEM_SOLVING.INTERPERSONAL.SITUATION",
                5,
                "누구와 어떤 상황에서 의견 차이나 문제가 발생했나요?",
                "자료 조사 범위와 회의 진행 방식을 두고 팀원들 간의 의견 대립 및 참여도 저하 발생",
            ),
            _slot(
                "PROBLEM_SOLVING.INTERPERSONAL.ACTION",
                5,
                "문제를 해결하기 위해 상대방과 어떻게 소통하고 어떤 행동을 취했나요?",
                "팀원들과 개별 면담을 통해 각자의 불만 사항과 상황을 청취. 이후 회의 시간 제한, 역할 재분배 등 모두가 동의할 수 있는 명확한 규칙 수립 및 제안",
            ),
            _slot(
                "PROBLEM_SOLVING.INTERPERSONAL.OUTCOME",
                5,
                "본인의 대응으로 인해 상대방의 반응이나 상황은 어떻게 변화하고 마무리되었나요?",
                "새로운 규칙 도입 후 팀원들이 불만을 해소하고 적극적으로 아이디어를 제시하기 시작했으며, 갈등 없이 기한 내에 최종 기획서 제출 완료",
            ),
            _slot(
                "PROBLEM_SOLVING.INTERPERSONAL.LEARNING",
                5,
                "이 과정을 통해 배운 점은 무엇이며, 향후 유사한 상황에 어떻게 적용할 계획인가요?",
                "상호 존중을 바탕으로 한 개별 소통과 명확한 규칙 수립이 팀워크에 미치는 긍정적인 영향을 배움. 향후 협업 시 초기 단계부터 명확한 역할 분담과 규칙을 세팅할 계획",
            ),
        ],
    ),
    _template(
        "PERFORMANCE",
        "성과 부진 개선",
        [
            _slot(
                "PROBLEM_SOLVING.PERFORMANCE.METRIC",
                5,
                "문제가 된 성과 지표는 무엇이며, 목표치와 실제 상태의 차이는 어느 정도였나요?",
                "뉴스레터 오픈율 목표치는 25%이지만, 12%에 머물러 있어 개선이 시급한 상황",
            ),
            _slot(
                "PROBLEM_SOLVING.PERFORMANCE.CAUSE",
                5,
                "목표에 도달하지 못한 근본적인 원인을 무엇으로 분석했나요?",
                "기존 구독자 데이터 분석 결과, 발송 시간대가 타깃의 주 활동 시간과 맞지 않고 제목이 길어 클릭을 유도하지 못함을 확인",
            ),
            _slot(
                "PROBLEM_SOLVING.PERFORMANCE.ACTION",
                5,
                "개선을 위해 기존 방식을 어떻게 변경하고 어떤 새로운 시도를 했나요?",
                "발송 시간을 출근 시간대로 변경하고, 제목을 15자 이내로 단축하여 핵심 키워드를 전면에 배치",
            ),
            _slot(
                "PROBLEM_SOLVING.PERFORMANCE.RESULT",
                5,
                "실행 후 지표는 어떻게 달라졌으며, 개선 효과를 어떻게 검증했나요?",
                "변경 후 오픈율 28%로 상승. A/B 테스트를 통해 제목 길이와 발송 시간의 상관관계를 교차 검증하여 효과 입증",
            ),
        ],
    ),
    _template(
        "TROUBLESHOOTING",
        "기술 트러블슈팅",
        [
            _slot(
                "PROBLEM_SOLVING.TROUBLESHOOTING.PROBLEM",
                5,
                "어떤 문제가 발생했으며, 그 문제가 미친 구체적인 영향 범위는 어디까지였나요?",
                "대규모 트래픽 발생 시 결제 페이지 로딩 속도가 5초 이상 지연되어 사용자의 결제 이탈 발생",
            ),
            _slot(
                "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE",
                5,
                "문제의 원인은 무엇이었으며, 이를 파악하기 위해 어떤 검증 과정을 거쳤나요?",
                "APM 툴을 활용해 병목 구간을 모니터링한 결과, 불필요한 데이터베이스 쿼리의 중복 호출이 원인임을 확인",
            ),
            _slot(
                "PROBLEM_SOLVING.TROUBLESHOOTING.SOLUTION",
                5,
                "어떤 해결책을 선택하여 적용했으며, 여러 방법 중 그 방법을 채택한 이유는 무엇인가요?",
                "쿼리 최적화 및 캐싱(Redis) 도입 선택. 서버 증설보다 비용 효율적이고 근본적인 성능 개선이 가능하기 때문",
            ),
            _slot(
                "PROBLEM_SOLVING.TROUBLESHOOTING.VERIFICATION",
                5,
                "해결 여부를 어떻게 검증했으며, 재발 방지를 위해 어떤 대책을 수립했나요?",
                "부하 테스트 도구로 시뮬레이션하여 응답 속도가 1초 이내로 단축됨을 확인. 이후 슬로우 쿼리 알림 모니터링 시스템 구축",
            ),
        ],
    ),
    _template(
        "FEEDBACK",
        "피드백 대응",
        [
            _slot(
                "PROBLEM_SOLVING.FEEDBACK.RECEIVED",
                5,
                "어떤 요청이나 불편 사항, 피드백이 반복적으로 접수되었나요?",
                "사내 비품 신청 과정이 전반적으로 어렵다는 불만이 다수 접수됨",
            ),
            _slot(
                "PROBLEM_SOLVING.FEEDBACK.NEED",
                5,
                "표면적인 의견 뒤에 있는 실제 니즈나 근본적인 문제점은 무엇으로 파악했나요?",
                "신청 양식 간소화뿐만 아니라, 신청 내역과 투명한 진행 상황 공유가 사용자들의 핵심 니즈임을 파악",
            ),
            _slot(
                "PROBLEM_SOLVING.FEEDBACK.ACTION",
                5,
                "이를 해결하기 위해 구체적으로 어떤 대응책이나 개선안을 실행했나요?",
                "Notion을 활용하여 신청 양식을 통일하고, 칸반 보드 형태로 처리 상태를 실시간으로 확인 가능하게 개선",
            ),
            _slot(
                "PROBLEM_SOLVING.FEEDBACK.OUTCOME",
                5,
                "조치 이후 피드백을 준 대상의 반응이나 상황은 어떻게 달라졌나요?",
                "비품 신청 관련 중복 문의가 80% 감소했으며, 팀원들로부터 업무 효율성과 투명성이 크게 높아졌다는 긍정적 피드백 확보",
            ),
        ],
    ),
    _template(
        "RECOVERY",
        "실패 회복",
        [
            _slot(
                "PROBLEM_SOLVING.RECOVERY.FAILURE",
                5,
                "아쉬웠던 결과, 구체적인 실수, 혹은 직면했던 한계는 무엇이었나요?",
                "첫 프로젝트 진행 시, 아이디어 기획에 과도한 시간을 쏟아 핵심 기능 구현을 기한 내에 마치지 못함",
            ),
            _slot(
                "PROBLEM_SOLVING.RECOVERY.CAUSE",
                5,
                "이러한 결과나 실수가 발생하게 된 핵심적인 원인은 무엇이라고 판단했나요?",
                "완벽한 결과물을 만들고자 하는 욕심으로 인해, MVP 정의와 작업의 우선순위 설정에 실패한 것이 원인",
            ),
            _slot(
                "PROBLEM_SOLVING.RECOVERY.EFFORT",
                5,
                "이를 극복하고 보완하기 위해 구체적으로 어떤 노력을 했나요?",
                "애자일 방법론과 스프린트 개념을 학습하고, 다음 프로젝트부터는 핵심 기능 위주로 백로그를 작성하여 일정 관리 방식을 개선",
            ),
            _slot(
                "PROBLEM_SOLVING.RECOVERY.CHANGE",
                5,
                "이전과 비교하여 결과가 어떻게 변화했나요?",
                "두 번째 프로젝트에서는 주어진 기한 내에 성공적으로 프로토타입을 배포하고 사용자 테스트까지 완료하며 목표 달성",
            ),
        ],
    ),
]


async def _test_template_catalog() -> dict[str, Any]:
    """에이전트 문서 3-0~3-4 의 확정 카탈로그(38개)를 그대로 반환한다.

    메인 서버 없이도 실제 구조화 흐름(카테고리 생성 → 앵커 → 하위 템플릿)을
    테스트 콘솔에서 볼 수 있게 한다.
    """
    return {
        "version": "agent-doc-3-0",
        "sections": [
            {
                "section_id": "DETAIL",
                "label": "상세정보",
                "slots": [
                    _slot(
                        "DETAIL.MOTIVATION",
                        4,
                        "어떤 계기로 이 경험을 시작했으며, 최종적으로 달성하고자 한 목표는 무엇인가요?",
                        "교내 커뮤니티의 비효율적인 게시판형 거래 방식을 개선하고, 전공 서적 거래의 편의성과 신뢰도를 높이기 위한 전용 플랫폼 기획 및 앱 리뉴얼",
                    ),
                    _slot(
                        "DETAIL.PERIOD",
                        4,
                        "전체 진행 기간은 언제부터 언제까지였나요?",
                        "2023.09 ~ 2023.12 (4개월)",
                    ),
                    _slot(
                        "DETAIL.ROLE",
                        4,
                        "본인의 역할은 무엇이었으며, 전체 인원과 역할 분담은 어떻게 구성되었나요?",
                        "기획 1명 (본인), 디자인 1명, 개발 2명 (총 4인 팀)",
                    ),
                    _slot(
                        "DETAIL.TARGET",
                        4,
                        "주요 타깃, 사용자, 혹은 고객은 누구였나요?",
                        "비싼 전공 서적 가격에 부담을 느끼며, 교내 직거래를 통해 택배비 절약과 빠른 거래를 원하는 대학생",
                    ),
                    _slot(
                        "DETAIL.STACK",
                        4,
                        "진행 과정에서 본인이 직접 활용한 기술, 방법론, 혹은 툴은 무엇인가요?",
                        "Figma, Notion, Slack, Google Analytics, IDI(심층 인터뷰), Usability Test",
                    ),
                ],
                "templates": [],
            },
            {
                "section_id": "ACHIEVEMENT",
                "label": "주요성과",
                "slots": [
                    _slot(
                        "ACHIEVEMENT.QUANTITATIVE",
                        4,
                        "수치로 증명할 수 있는 정량적인 성과는 무엇인가요?",
                        "리뉴얼 전 대비 DAU(일간 활성 사용자) 150% 증가",
                    ),
                    _slot(
                        "ACHIEVEMENT.QUALITATIVE",
                        4,
                        "간접적인 지표로 확인할 수 있는 정성적인 성과는 무엇인가요?",
                        '"검색부터 구매 약속까지 과정이 직관적이다"라는 사용자 피드백 다수 확보',
                    ),
                ],
                "templates": [],
            },
            {
                "section_id": "TASK",
                "label": "담당업무",
                "slots": [
                    {
                        **_slot(
                            "TASK.SUMMARY",
                            4,
                            "담당한 주요 업무 또는 역할을 적어주세요.",
                            "사용자 리서치 및 문제 정의",
                        ),
                        "is_anchor": True,
                    }
                ],
                "templates": [_TASK_BASIC],
            },
            {
                "section_id": "PROBLEM_SOLVING",
                "label": "문제해결",
                "slots": [
                    {
                        **_slot(
                            "PROBLEM_SOLVING.SUMMARY",
                            4,
                            "문제해결 에피소드를 한 줄로 요약해 주세요.",
                            "신규 프로모션 페이지 가입 이탈 문제 해결",
                        ),
                        "is_anchor": True,
                    }
                ],
                "templates": _PROBLEM_SOLVING_TEMPLATES,
            },
            {
                "section_id": "LEARNING",
                "label": "배운 점",
                "slots": [
                    _slot(
                        "LEARNING.GROWTH",
                        4,
                        "이 경험을 통해 새롭게 배우거나 성장한 점은 무엇이며, 향후 어떻게 활용할 계획인가요?",
                        "이번 프로젝트에서는 구글 애널리틱스를 기초적으로만 활용했지만, 향후에는 SQL을 학습하여 직접 DB에서 데이터를 추출하고 더 정교하게 사용자 행동 데이터를 분석해 보고 싶다.",
                    )
                ],
                "templates": [],
            },
        ],
    }


def create_test_template_catalog_client() -> TemplateCatalogClient:
    """테스트 UI 전용 카탈로그 클라이언트를 생성한다."""
    return TemplateCatalogClient(_test_template_catalog)


@dataclass
class _UserMap:
    version: int
    rows: list[MapBlockRow]


class InMemoryTestMapStore:
    """사용자별 샘플 맵을 메모리에 유지한다."""

    def __init__(self) -> None:
        self._maps: dict[str, _UserMap] = {}
        self._lock = asyncio.Lock()

    async def snapshot(self, user_id: str) -> ExperienceMapSnapshot:
        """사용자 맵을 만들거나 현재 스냅샷을 반환한다."""
        async with self._lock:
            current = self._maps.setdefault(user_id, _UserMap(version=1, rows=_initial_rows()))
            return build_map_snapshot(list(current.rows), current.version)

    async def display_map(self, user_id: str) -> dict[str, Any]:
        """테스트 UI가 블록을 선택할 수 있는 안전한 표시 모델을 반환한다."""
        snapshot = await self.snapshot(user_id)
        activities = []
        for group in snapshot.outline():
            for activity in group["children"]:
                context = snapshot.get_activity_context(activity["alias"])
                if context is None:
                    continue
                activities.append(
                    {
                        "id": context.activity_id,
                        "title": activity["title"],
                        "tree": context.tree_text,
                    }
                )
        return {"map_version": snapshot.map_version, "activities": activities}

    async def commit(self, state: ExperienceMapState) -> ExperienceMapState:
        """검증된 operation을 샘플 맵에 반영하고 커밋 결과를 만든다."""
        user_id = str(state["user_id"])
        async with self._lock:
            current = self._maps.setdefault(user_id, _UserMap(version=1, rows=_initial_rows()))
            by_id = {row.block_id: row for row in current.rows}
            aliases = state.get("alias_to_block_id", {})
            previous_version = current.version
            applied: list[AppliedItem] = []

            for raw in state.get("commit_items", []):
                item = StructuredItem.model_validate(raw)
                if item.action == "update":
                    target_id = aliases.get(item.target_ref or "")
                    if target_id is None or target_id not in by_id:
                        raise ValueError("테스트 맵에서 수정 대상 블록을 찾을 수 없습니다.")
                    old = by_id[target_id]
                    replacement = MapBlockRow(
                        block_id=old.block_id,
                        parent_id=old.parent_id,
                        level=old.level,
                        kind=old.kind,
                        position=old.position,
                        content=item.text,
                        placeholder=old.placeholder,
                        is_text_editable=old.is_text_editable,
                        is_deletable=old.is_deletable,
                    )
                    current.rows[current.rows.index(old)] = replacement
                    by_id[target_id] = replacement
                    applied.append(
                        AppliedItem(
                            item_id=item.item_id, block_id=target_id, path=_path(by_id, target_id)
                        )
                    )
                    continue

                parent_id = aliases.get(item.parent_ref or "")
                if parent_id is None or parent_id not in by_id:
                    raise ValueError("테스트 맵에서 추가 대상 부모 블록을 찾을 수 없습니다.")
                parent = by_id[parent_id]
                new_id = str(max((int(key) for key in by_id if key.isdecimal()), default=999) + 1)
                position = (
                    max(
                        (row.position for row in current.rows if row.parent_id == parent_id),
                        default=0,
                    )
                    + 1
                )
                added = MapBlockRow(
                    block_id=new_id,
                    parent_id=parent_id,
                    level=parent.level + 1,
                    kind="CONTENT",
                    position=position,
                    content=item.text,
                    placeholder=None,
                    is_text_editable=True,
                    is_deletable=True,
                )
                current.rows.append(added)
                by_id[new_id] = added
                applied.append(
                    AppliedItem(item_id=item.item_id, block_id=new_id, path=_path(by_id, new_id))
                )

            current.version += 1
            result = CommitResult(
                request_id=str(state["request_id"]),
                previous_version=previous_version,
                map_version=current.version,
                revert_to_version=previous_version,
                can_revert=False,
                applied=applied,
                dropped=[],
            )
        return {**state, "commit_result": result.model_dump(mode="json")}


# 경로는 level 2 활동부터 시작한다. level 1 은 전체 맵의 루트라 자리 정보가 없다.
_PATH_MIN_LEVEL = 2


def _path(rows: dict[str, MapBlockRow], block_id: str) -> str:
    """결과 응답용 블록 경로를 만든다. **블록 자신은 넣지 않는다.**

    명세 4-2 의 `path` 는 `"교내 커머스 리뉴얼 > 문제해결"` 처럼 블록이 **놓인
    자리**를 가리킨다. 자신을 넣으면 방금 만든 문장이 카테고리 자리에 들어가
    `"교내 커머스 리뉴얼 > 이탈률이 90% 감소함.에 1개를 정리했어요."` 처럼 읽힌다.

    level 1 최상위 루트도 뺀다. 남기면 `_path_parts` 가 그걸 활동명으로 읽어
    `"프로젝트 경험 > 성과"` 가 된다. 경로의 시작은 언제나 level 2 활동이다.
    """
    labels: list[str] = []
    parent_id = rows[block_id].parent_id
    while parent_id is not None:
        current = rows[parent_id]
        if current.level >= _PATH_MIN_LEVEL and current.content:
            labels.append(current.content)
        parent_id = current.parent_id
    labels.reverse()
    return " > ".join(labels)


class TestUiGraphRunner(GraphRunner):
    """실제 LLM graph와 메모리 커밋을 조합한 테스트 전용 실행기."""

    def __init__(self, store: InMemoryTestMapStore) -> None:
        self._store = store
        self._runner = CheckpointGraphRunner(
            build_graph(checkpointer=InMemorySaver()), state_events=self._state_events
        )

    async def run(self, state: ExperienceMapState):
        async for event in self._runner.run(state):
            yield event

    async def resume(self, state: ExperienceMapState):
        async for event in self._runner.resume(state):
            yield event

    async def _state_events(self, state: ExperienceMapState):
        if state.get("fallback_reason"):
            yield MessageCompleteEvent(
                message=CompletedMessage(
                    request_id=str(state["request_id"]),
                    session_id=str(state["session_id"]),
                    response_kind="fallback",
                    ai_response=fallback_message(state.get("fallback_reason")),
                    committed=False,
                )
            )
            return
        if state.get("commit_items"):
            async for event in coordinate(state, commit_runner=self._store.commit):
                yield event


_store = InMemoryTestMapStore()


def get_test_map_store() -> InMemoryTestMapStore:
    """테스트 UI 프로세스의 맵 저장소를 반환한다."""
    return _store


@dataclass
class InMemoryObjectStore:
    """`ObjectStore` 를 메모리로 흉내낸다.

    테스트 UI는 그래프·경험 맵을 in-memory 로 바꾸지만 파일 업로드는 여전히
    `get_upload_store()` 를 통해 **진짜 GCS** 를 부른다. 로컬·데모 환경에는
    `EXPMAP_UPLOAD_BUCKET`이나 GCP 인증이 없는 경우가 많아, 파일을 하나라도
    첨부하면 `chat_stream` 이 잡지 못하는 예외(`RuntimeError` 또는
    `google.auth` 예외)로 500 이 난다. `ExperienceMapError` 만 잡기 때문이다.

    GCS 인증·네트워크가 필요 없다는 점 외에는 실제 저장소와 동작이 같다.
    """

    objects: dict[str, bytes] = field(default_factory=dict)
    created: dict[str, datetime] = field(default_factory=dict)

    async def upload(self, object_name: str, data: bytes, content_type: str) -> None:
        self.objects[object_name] = data
        self.created[object_name] = datetime.now(UTC)

    async def download(self, object_name: str) -> bytes:
        return self.objects[object_name]

    async def delete(self, object_name: str) -> None:
        self.objects.pop(object_name, None)
        self.created.pop(object_name, None)

    async def list_names(self, prefix: str) -> list[str]:
        return [name for name in self.objects if name.startswith(prefix)]

    async def created_at(self, object_name: str) -> datetime | None:
        return self.created.get(object_name)


__all__ = [
    "InMemoryObjectStore",
    "InMemoryTestMapStore",
    "TestUiGraphRunner",
    "create_test_template_catalog_client",
    "get_test_map_store",
]
