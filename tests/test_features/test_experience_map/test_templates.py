"""템플릿 카탈로그 클라이언트 테스트"""

import asyncio

import pytest
from pydantic import ValidationError

from features.experience_map.templates import TemplateCatalog, TemplateCatalogClient


def sample_catalog(*, version: str = "2026-08-09") -> dict:
    """명세 7절 구조를 따르는 최소 카탈로그"""
    return {
        "version": version,
        "sections": [
            {
                "section_id": "DETAIL",
                "label": "상세정보",
                "slots": [
                    {
                        "slot_id": "DETAIL.MOTIVATION",
                        "level": 4,
                        "placeholder": "시작 계기",
                        "example": "문제를 해결하고 싶었습니다.",
                    }
                ],
                "templates": [],
            },
            {
                "section_id": "PROBLEM_SOLVING",
                "label": "문제해결",
                "slots": [
                    {
                        "slot_id": "PROBLEM_SOLVING.SUMMARY",
                        "level": 4,
                        "is_anchor": True,
                        "placeholder": "문제 요약",
                        "example": "가입 이탈 문제 해결",
                    }
                ],
                "templates": [
                    {
                        "template_id": "TROUBLESHOOTING",
                        "label": "기술 트러블슈팅",
                        "slots": [
                            {
                                "slot_id": "PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE",
                                "level": 5,
                                "placeholder": "원인",
                                "example": "APM으로 병목을 확인했습니다.",
                            }
                        ],
                    }
                ],
            },
        ],
    }


@pytest.mark.asyncio
async def test_caches_catalog_until_ttl_expires():
    """TTL 안에서는 HTTP 조회를 반복하지 않는다."""
    calls = 0
    now = 100.0

    async def fetcher():
        nonlocal calls
        calls += 1
        return sample_catalog(version=f"v{calls}")

    client = TemplateCatalogClient(fetcher, ttl_seconds=10, clock=lambda: now)

    assert (await client.get_catalog()).version == "v1"
    now = 109.9
    assert (await client.get_catalog()).version == "v1"
    now = 110.0
    assert (await client.get_catalog()).version == "v2"
    assert calls == 2


@pytest.mark.asyncio
async def test_concurrent_initial_fetch_is_single_flight():
    """동시 요청이 와도 실제 카탈로그 조회는 한 번뿐이다."""
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetcher():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return sample_catalog()

    client = TemplateCatalogClient(fetcher)
    tasks = [asyncio.create_task(client.get_catalog()) for _ in range(8)]
    await started.wait()
    release.set()

    catalogs = await asyncio.gather(*tasks)

    assert calls == 1
    assert all(catalog.version == "2026-08-09" for catalog in catalogs)


@pytest.mark.asyncio
async def test_refresh_ignores_ttl_and_fetches_again():
    """unknown_slot_id 대응용 강제 갱신은 TTL을 무시한다."""
    calls = 0

    async def fetcher():
        nonlocal calls
        calls += 1
        return sample_catalog(version=f"v{calls}")

    client = TemplateCatalogClient(fetcher)

    await client.get_catalog()
    refreshed = await client.refresh()

    assert refreshed.version == "v2"
    assert calls == 2


@pytest.mark.asyncio
async def test_failed_first_fetch_does_not_poison_later_retry():
    """기동 시에는 조회하지 않고, 첫 사용 실패 뒤 다음 사용에서 재시도한다."""
    calls = 0

    async def fetcher():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("메인 서버가 일시적으로 응답하지 않습니다.")
        return sample_catalog()

    client = TemplateCatalogClient(fetcher)

    with pytest.raises(RuntimeError, match="일시적으로"):
        await client.get_catalog()

    assert (await client.get_catalog()).version == "2026-08-09"
    assert calls == 2


@pytest.mark.asyncio
async def test_get_slot_returns_placeholder_and_example():
    """few-shot 프롬프트에 쓸 슬롯 정보를 조회한다."""

    async def fetcher():
        return sample_catalog()

    client = TemplateCatalogClient(fetcher)

    slot = await client.get_slot("PROBLEM_SOLVING.TROUBLESHOOTING.CAUSE")

    assert slot is not None
    assert slot.level == 5
    assert slot.placeholder == "원인"
    assert await client.get_slot("UNKNOWN.SLOT") is None


def test_catalog_rejects_invalid_slot_hierarchy():
    """카테고리 슬롯과 템플릿 슬롯의 level 혼입을 막는다."""
    payload = sample_catalog()
    payload["sections"][0]["slots"][0]["level"] = 5

    with pytest.raises(ValidationError, match="section.slots"):
        TemplateCatalog.model_validate(payload)


def test_catalog_rejects_duplicate_slot_ids():
    """동일 slot_id면 어느 placeholder를 쓸지 모호하다."""
    payload = sample_catalog()
    payload["sections"][1]["slots"][0]["slot_id"] = "DETAIL.MOTIVATION"

    with pytest.raises(ValidationError, match="중복된 slot_id"):
        TemplateCatalog.model_validate(payload)
