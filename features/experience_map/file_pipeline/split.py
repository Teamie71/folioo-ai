"""파일 원문을 배정 단위(줄)로 나누고 확실한 노이즈를 걸러낸다.

LLM이 원문을 다시 옮겨 적게 하면 한 글자만 달라도 원문 대조에 실패해 내용이
통째로 사라지거나 조각이 수백 개로 불어났다(dev 재현, 2026-09-30). 그래서 파일은
코드가 먼저 줄 단위로 나누고 번호를 붙이며, LLM은 번호별로 칸만 고른다.
"""

import re
from dataclasses import dataclass

_BULLET = re.compile(r"^\s*(?:[-•·*▪◦▶]\s|\d+[.)]\s|\[[^\[\]]{1,60}\]\s*$|\||A[.:]\s)")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}")
_TERMINAL = re.compile(r"(?:[.!?。)）%|]|다|요|음|함|됨|임)\s*$")
_MID_SENTENCE = re.compile(r"(?:[어고며서는은을를의에과와로이가및,·]|하여|하고|해서)\s*$")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)")
_HEADING_PREV_JOIN = re.compile(r"(?:[을를의,]|에서|으로|하여|하며|해서|하고|[가-힣]는)\s*$")
"""제목처럼 짧은 앞 줄도 이 어미로 끝나면 문장이 이어진다("…되어야 함을" + "배웠습니다.").
"과·가·로"처럼 명사 끝("성과", "평가")과 겹치는 글자는 넣지 않는다."""
_PARTICLE_START = re.compile(r"^(?:[을를은는이가의에와과로]|으로|에서)\s")
"""조사로 시작하는 줄은 앞 줄에서 끊긴 것이다("클릭률(CTR)" + "을 높였습니다.")."""
_CAPTION = re.compile(r"^\s*[▲▼△▽]")
"""사진 설명. 앞뒤 줄과 잇지 않는다."""
_FACT = re.compile(
    r"\d[\d,.]*\s*(?:만\s?원|억|원|%|건|명|개|°C|배|점|회|시간|분|초|위|kg|위안|달러)"
    r"|[\"“][^\"”]{2,}[\"”]"
)
"""수치("250만원", "99.9°C")나 인용("공부할 곳이 없어요")이 든 줄. 짧아도 제목이 아니다 —
포트폴리오 PDF의 지표 카드가 한 줄씩 떨어져 제목으로 버려졌다(실제 포트폴리오 재현)."""
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")
_ENUMERATOR = re.compile(r"(?:^|\s)(?:[A-Za-z]{1,8}\s?)?\d{1,2}[.)]$")
"""문장 나누기로 떨어진 번호 표시("P1.", "Project 2.", "3."). 다음 조각에 붙인다."""
_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$")
"""PDF 목록 번호가 "1", ".", "Hook: …"으로 쪼개질 때 남는 문장부호만 있는 줄."""
_SYSTEM_NOTE = re.compile(r"\[(?:페이지가 많아|내용이 길어)[^\]]*\]")
"""파일처리 단계가 원문 뒤에 붙이는 잘림 안내문(extractors). 원문 내용이 아니다."""

HEADING_MAX_CHARS = 30
"""제목으로 볼 수 있는 줄의 최대 길이. 이보다 길면 내용으로 본다."""

SECTION_NAMES = frozenset(
    {
        "상세정보",
        "담당업무",
        "문제해결",
        "문제해결경험",
        "주요성과",
        "성과",
        "배운점",
        "느낀점",
        "트러블슈팅",
        "결과",
    }
)
"""문서에서 구획 이름으로만 쓰이는 단어. 이 줄은 항상 제목이다 — 긴 문서를 나눠
배정할 때 청크 첫 줄이 "문제해결"이면 모델이 에피소드 요약으로 착각했다."""

_CODE_NOISE = (
    re.compile(r"^[-–—\s]*\d{1,3}[-–—\s]*$"),  # 쪽 번호
    re.compile(r"\b01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}\b"),  # 휴대폰 번호
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),  # 이메일
    re.compile(r"(?i)confidential|무단\s*(?:배포|복제)"),  # 머리말·꼬리말 문구
)
_REQUEST_PHRASE = re.compile(r"정리해\s*(?:주세요|줘|주라)|정리\s*부탁")
"""파일 안의 작업 요청 문구("아래 경험을 정리해 주세요"). 채팅 메시지에서는 빼지 않는다 —
"2번 프로젝트만 정리해줘"처럼 지시가 함께 들어 있어 칸 배정 LLM이 지시로 읽어야 한다."""


_PROJECT_TITLE = re.compile(
    r"^\s*(?:P|Project|PROJECT|Part|PART|Case|CASE|프로젝트)\s?(\d{1,2})(?!\d)"
)


def project_number(line: str) -> int | None:
    """ "P2. 굿즈 공동구매", "Project 2. 학과 굿즈 공동구매"처럼 프로젝트 번호로 시작하는 줄의 번호.

    포트폴리오는 앞쪽 목차와 본문, 그리고 페이지마다 같은 프로젝트를 번호로 다시
    적는다. 번호가 같으면 같은 프로젝트다.
    """
    match = _PROJECT_TITLE.match(line)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class SplitDocument:
    """나눈 결과. `lines`는 배정 대상, `noise`는 코드가 확실한 노이즈로 뺀 줄."""

    lines: list[str]
    noise: list[str]


def heading_like(line: str) -> bool:
    """짧고 문장이 끝나지 않은 줄(또는 짧은 질문)인지 본다. 불릿 줄은 제목이 아니다."""
    if _BULLET.match(line) and not _NUMBERED.match(line):
        return False
    if _FACT.search(line) and project_number(line) is None:
        return False
    body = re.sub(r"^\s*(?:\d+[.)]\s*|#+\s*)", "", line)
    # "문제 진단 (Analysis)"처럼 끝의 괄호 설명은 문장 끝으로 보지 않는다.
    stem = _TRAILING_PAREN.sub("", body) or body
    return len(body) <= HEADING_MAX_CHARS and (
        not _TERMINAL.search(stem) or body.rstrip().endswith("?")
    )


def is_section_name(line: str) -> bool:
    """ "## 문제 해결", "3. 주요 성과"처럼 구획 이름만 있는 줄인지 본다."""
    return re.sub(r"[^가-힣]", "", re.sub(r"^[\s#\d.)]*", "", line)) in SECTION_NAMES


def is_code_noise(line: str, *, request_phrases: bool = True) -> bool:
    """쪽 번호·연락처·머리말 문구·작업 요청처럼 모양만으로 확실한 노이즈인지 본다."""
    if request_phrases and _REQUEST_PHRASE.search(line):
        return True
    return any(pattern.search(line) for pattern in _CODE_NOISE)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _expand_tables(lines: list[str]) -> list[str]:
    """마크다운 표를 배정 단위로 푼다.

    3칸 이상 표는 칸마다 다른 슬롯일 수 있어 "머리글: 값"으로 나눈다(문제|해결|결과).
    2칸 표는 한 행을 "키: 값" 한 줄로 만든다. 머리행은 제목 후보로 남긴다.
    """
    out: list[str] = []
    header: list[str] | None = None
    for line in lines:
        if not line.startswith("|"):
            header = None
            out.append(line)
            continue
        cells = _cells(line)
        if header is None or len(cells) != len(header):
            header = cells
            out.append(" | ".join(cells))
            continue
        if len(cells) == 2:
            out.append(f"{cells[0]}: {cells[1]}")
        else:
            out.extend(
                f"{title}: {cell}" for title, cell in zip(header, cells, strict=False) if cell
            )
    return out


def _joins_previous(previous: str, line: str) -> bool:
    """PDF 줄바꿈으로 끊긴 문장의 다음 줄인지 본다.

    앞 줄이 문장 끝으로 끝나지 않았고, 이번 줄이 불릿·구획 이름·사진 설명이 아니어야
    한다. 조사로 시작하는 줄은 항상 잇는다.
    이번 줄이 제목처럼 짧으면 앞 줄이 연결 어미("…이끌어")로 끝날 때만 잇는다 —
    "…판단" 다음의 "배운 점"을 이어 붙이지 않기 위해서다.
    """
    if _BULLET.match(line) or _CAPTION.match(previous) or _CAPTION.match(line):
        return False
    if is_code_noise(previous) or is_code_noise(line):
        return False  # 쪽 번호("- 1 -") 뒤 본문이 이어 붙으면 본문까지 노이즈로 빠진다
    if ": " in line[:12] or is_section_name(line):
        return False
    if _PARTICLE_START.match(line):
        return True
    if _TERMINAL.search(previous):
        return False
    if heading_like(previous) and not _HEADING_PREV_JOIN.search(previous):
        return False
    return not heading_like(line) or bool(_MID_SENTENCE.search(previous))


def split_document(text: str, *, drop_request_phrases: bool = True) -> SplitDocument:
    """원문을 배정 단위로 나누고 확실한 노이즈를 뺀다.

    여러 문장이 한 줄에 있으면 문장마다 나눈다("Q.", "P1." 같은 번호 조각은 다음
    문장에 붙인다). 원문 문자는 고치지 않고 공백·줄 경계만 바꾼다. 파일처리 단계의
    잘림 안내문과 문장부호만 있는 줄은 뺀다.
    """
    text = _SYSTEM_NOTE.sub("", text)
    raw_lines = [line.strip() for line in text.splitlines()]
    raw_lines = [
        line
        for line in raw_lines
        if line and not _TABLE_SEPARATOR.match(line) and not _PUNCTUATION_ONLY.match(line)
    ]

    units: list[str] = []
    for line in _expand_tables(raw_lines):
        if units and _joins_previous(units[-1], line):
            units[-1] = f"{units[-1]} {line}"
        else:
            units.append(line)

    lines: list[str] = []
    for unit in units:
        parts = [part.strip() for part in _SENTENCE_BOUNDARY.split(unit) if part.strip()]
        merged: list[str] = []
        for part in parts:
            if merged and (len(merged[-1]) <= 2 or _ENUMERATOR.search(merged[-1])):
                merged[-1] = f"{merged[-1]} {part}"
            else:
                merged.append(part)
        lines.extend(merged)

    noise = [line for line in lines if is_code_noise(line, request_phrases=drop_request_phrases)]
    noise_set = set(noise)
    return SplitDocument(lines=[line for line in lines if line not in noise_set], noise=noise)
