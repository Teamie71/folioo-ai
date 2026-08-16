"""경험정리 템플릿 카탈로그 클라이언트

카탈로그의 소유권은 메인 서버에 있다. AI 서버는 `GET /templates` 응답을 1시간
캐시하고, 구조화·정제 노드에서 slot_id의 placeholder와 예시를 조회한다.

**응답 형태를 좁게 검증하지 않는다.** 소유자가 메인 서버라 배치 규칙이 바뀔 수
있고, 실제로 명세 7절 예시와 9절 카탈로그 정의가 서로 다르다.

| 항목 | 7절 예시 | 9절 정의 |
| --- | --- | --- |
| 카테고리 슬롯 10개 | 담을 자리 없음 | `{SECTION}.{SLOT}` 형태로 존재 |
| 템플릿 안 slot level | 4·5 혼재 | 점 개수가 곧 level |
| `is_anchor` | 없음 | `TASK.SUMMARY`·`PROBLEM_SOLVING.SUMMARY` 가 앵커 |

여기서는 **둘 다 받는다.** 우리가 검증할 것은 배치가 아니라 "AI 가 보낼 slot_id 를
찾을 수 있는가" 뿐이다. 중복 slot_id 만 막는다 — 그건 잘못된 placeholder 를
참조하게 만든다.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field, model_validator

from common.http_client import request_with_retry
from features.experience_map.config import TEMPLATE_CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)

TEMPLATES_PATH = "/api/v1/experience-map/templates"

CatalogFetcher = Callable[[], Awaitable[dict]]
Clock = Callable[[], float]

ANCHOR_SLOT_IDS = frozenset({"TASK.SUMMARY", "PROBLEM_SOLVING.SUMMARY"})
"""level 5 를 매달 수 있는 level 4 앵커 (명세 9절).

메인 서버가 `is_anchor` 를 보내면 그 값이 우선이다. 여기 목록은 필드가 아예 오지
않을 때만 쓰는 폴백이다 — 명세 7절 응답 예시에 `is_anchor` 가 없어서, 이게 없으면
모든 슬롯이 앵커가 아닌 것으로 읽혀 구조화 프롬프트가 앵커를 표시하지 못한다.
"""


class TemplateSlot(BaseModel):
    """카탈로그의 한 작성 슬롯"""

    slot_id: str = Field(min_length=1)
    level: int = Field(ge=4, le=5)
    placeholder: str = Field(min_length=1)
    example: str = Field(min_length=1)
    is_anchor: bool = False

    @model_validator(mode="after")
    def _fill_anchor(self) -> "TemplateSlot":
        """`is_anchor` 가 응답에 없으면 slot_id 로 채운다."""
        if "is_anchor" not in self.model_fields_set:
            self.is_anchor = self.slot_id in ANCHOR_SLOT_IDS
        return self


class TemplateDefinition(BaseModel):
    """하위 템플릿의 슬롯 묶음

    **level 을 제한하지 않는다.** 명세 7절 응답 예시가 앵커 성격의 level 4 슬롯을
    템플릿 안에 함께 넣는다. level 5 만 받도록 막아 두면 실제 응답이 파싱 단계에서
    거부된다. level 별 배치 규칙은 카탈로그 소유자인 메인 서버가 정한다.
    """

    template_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    slots: list[TemplateSlot] = Field(default_factory=list)


class TemplateSection(BaseModel):
    """카테고리별 level 4 슬롯과 하위 템플릿

    `slots` 는 선택이다. 명세 7절 예시에는 없지만 9절이 정의한 카테고리 슬롯 10개
    (`DETAIL.MOTIVATION` 등)는 어떤 템플릿에도 속하지 않으므로 이 자리로 온다.
    """

    section_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    slots: list[TemplateSlot] = Field(default_factory=list)
    templates: list[TemplateDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_slot_levels(self) -> "TemplateSection":
        """카테고리 직속 슬롯에는 level 4만 허용한다."""
        if any(slot.level != 4 for slot in self.slots):
            raise ValueError("section.slots에는 level 4 슬롯만 올 수 있습니다.")
        return self


class TemplateCatalog(BaseModel):
    """메인 서버가 제공하는 전체 템플릿 카탈로그"""

    version: str = Field(min_length=1)
    sections: list[TemplateSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_slot_ids(self) -> "TemplateCatalog":
        """slot_id가 중복되면 AI가 잘못된 placeholder를 참조할 수 있다."""
        slot_ids = [slot.slot_id for slot in self.iter_slots()]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("카탈로그에 중복된 slot_id가 있습니다.")
        return self

    def iter_slots(self):
        """카테고리와 하위 템플릿 슬롯을 문서 순서로 순회한다."""
        for section in self.sections:
            yield from section.slots
            for template in section.templates:
                yield from template.slots

    def get_slot(self, slot_id: str) -> TemplateSlot | None:
        """slot_id에 해당하는 슬롯을 찾는다."""
        return next((slot for slot in self.iter_slots() if slot.slot_id == slot_id), None)


async def _fetch_template_catalog() -> dict:
    """메인 서버에서 카탈로그를 조회한다."""
    result = await request_with_retry("GET", TEMPLATES_PATH)
    if not isinstance(result, dict):
        raise ValueError("템플릿 카탈로그 응답이 객체가 아닙니다.")
    return result


class TemplateCatalogClient:
    """TTL 캐시와 single-flight를 갖는 템플릿 카탈로그 클라이언트"""

    def __init__(
        self,
        fetcher: CatalogFetcher | None = None,
        *,
        ttl_seconds: float = TEMPLATE_CACHE_TTL_SECONDS,
        clock: Clock = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("템플릿 카탈로그 TTL은 0보다 커야 합니다.")

        self._fetcher = fetcher or _fetch_template_catalog
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._catalog: TemplateCatalog | None = None
        self._cached_at: float | None = None
        self._lock = asyncio.Lock()

    async def get_catalog(self) -> TemplateCatalog:
        """유효한 캐시를 반환하고, 만료됐으면 한 번만 다시 조회한다."""
        if self._is_fresh():
            return self._catalog  # type: ignore[return-value]

        async with self._lock:
            # lock 대기 중 다른 요청이 이미 갱신했을 수 있다.
            if self._is_fresh():
                return self._catalog  # type: ignore[return-value]
            return await self._refresh_locked()

    async def refresh(self) -> TemplateCatalog:
        """TTL과 무관하게 카탈로그를 강제 갱신한다.

        `unknown_slot_id`를 받은 커밋 경로가 최신 카탈로그를 확인할 때 쓴다.
        """
        async with self._lock:
            return await self._refresh_locked()

    async def get_slot(self, slot_id: str) -> TemplateSlot | None:
        """현재 카탈로그에서 slot_id의 placeholder·예시를 찾는다."""
        catalog = await self.get_catalog()
        return catalog.get_slot(slot_id)

    async def _refresh_locked(self) -> TemplateCatalog:
        raw_catalog = await self._fetcher()
        catalog = TemplateCatalog.model_validate(raw_catalog)
        self._catalog = catalog
        self._cached_at = self._clock()
        logger.info("템플릿 카탈로그 갱신 완료 (version=%s)", catalog.version)
        return catalog

    def _is_fresh(self) -> bool:
        """캐시가 있고 TTL이 지나지 않았는지 확인한다."""
        if self._catalog is None or self._cached_at is None:
            return False
        return self._clock() - self._cached_at < self._ttl_seconds


_template_catalog_client: TemplateCatalogClient | None = None


def get_template_catalog_client() -> TemplateCatalogClient:
    """애플리케이션 공유 카탈로그 클라이언트를 반환한다."""
    global _template_catalog_client
    if _template_catalog_client is None:
        _template_catalog_client = TemplateCatalogClient()
    return _template_catalog_client


def set_template_catalog_client(client: TemplateCatalogClient | None) -> None:
    """카탈로그 클라이언트를 주입한다 (테스트·기동 시)."""
    global _template_catalog_client
    _template_catalog_client = client


__all__ = [
    "TEMPLATES_PATH",
    "TemplateCatalog",
    "TemplateCatalogClient",
    "TemplateDefinition",
    "TemplateSection",
    "TemplateSlot",
    "get_template_catalog_client",
    "set_template_catalog_client",
]
