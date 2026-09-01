"""LLM 응답을 흘려 받으며 완성된 활동 객체를 뽑아내는 파서"""

import json

_ACTIVITIES_KEY = '"activities"'


class ActivityJsonStreamParser:
    """부분 JSON 텍스트에서 `activities` 배열의 완성된 원소를 순서대로 뽑아낸다.

    LLM 응답은 `{"activities": [{...}, {...}]}` 형태로 도착한다. 전체가 도착하기를
    기다리지 않고, 원소 하나의 중괄호가 닫히는 순간 그 원소만 떼어 낸다.

    문자열 안의 중괄호와 이스케이프를 구분해야 하므로 depth 만 세지 않고
    `in_string`·`escaped` 상태를 함께 추적한다.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._scan_from = 0
        self._array_started = False
        self._object_start: int | None = None
        self._depth = 0
        self._in_string = False
        self._escaped = False
        self._finished = False

    def feed(self, chunk: str) -> list[dict]:
        """텍스트 조각을 넣고, 이번 호출에서 새로 완성된 원소들을 반환한다.

        Args:
            chunk: LLM 이 새로 뱉은 텍스트 조각

        Returns:
            list[dict]: 새로 완성된 활동 객체 목록 (없으면 빈 리스트)
        """
        if not chunk or self._finished:
            return []

        self._buffer += chunk
        completed: list[dict] = []

        if not self._array_started and not self._locate_array_start():
            return completed

        index = self._scan_from
        while index < len(self._buffer):
            char = self._buffer[index]

            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._in_string = False
                index += 1
                continue

            if char == '"':
                self._in_string = True
            elif char == "{":
                if self._depth == 0:
                    self._object_start = index
                self._depth += 1
            elif char == "}":
                self._depth -= 1
                if self._depth == 0 and self._object_start is not None:
                    raw = self._buffer[self._object_start : index + 1]
                    self._object_start = None
                    parsed = _try_parse_object(raw)
                    if parsed is not None:
                        completed.append(parsed)
            elif char == "]" and self._depth == 0:
                # activities 배열이 닫혔다. 뒤따르는 텍스트는 볼 필요가 없다.
                self._finished = True
                index += 1
                break

            index += 1

        self._scan_from = index
        return completed

    def _locate_array_start(self) -> bool:
        """`"activities"` 키 뒤의 `[` 를 찾아 스캔 시작 지점을 잡는다."""
        key_index = self._buffer.find(_ACTIVITIES_KEY)
        if key_index == -1:
            return False

        bracket_index = self._buffer.find("[", key_index + len(_ACTIVITIES_KEY))
        if bracket_index == -1:
            return False

        self._array_started = True
        self._scan_from = bracket_index + 1
        return True


def _try_parse_object(raw: str) -> dict | None:
    """완성된 것으로 보이는 JSON 객체 문자열을 파싱한다.

    깨진 조각이 섞여 들어와도 스트림 전체를 중단시키지 않도록 실패는 조용히 건너뛴다.
    최종 검증은 호출자가 Pydantic 으로 수행한다.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


__all__ = ["ActivityJsonStreamParser"]
