"""DART 공시검색 API를 호출한다. 텔레그램 전송은 하지 않는다."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
PAGE_COUNT = 100
MAX_PAGES = 10
TIMEOUT_SECONDS = 10


class DartError(Exception):
    """사람이 읽을 수 있는 DART 조회 오류. 메시지에 API 키를 넣지 않는다."""


@dataclass
class Disclosure:
    rcept_no: str
    corp_name: str
    stock_code: str
    report_nm: str


@dataclass
class DisclosurePage:
    items: list[Disclosure]
    truncated: bool
    empty: bool


def today_yyyymmdd() -> str:
    """오늘 날짜를 한국 시간 기준 YYYYMMDD로 돌려준다."""
    try:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        now = datetime.now(timezone(timedelta(hours=9)))
    return now.strftime("%Y%m%d")


def _request_page(api_key: str, bgn_de: str, end_de: str, page_no: int) -> dict:
    try:
        response = requests.get(
            LIST_URL,
            params={
                "crtfc_key": api_key,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": str(page_no),
                "page_count": str(PAGE_COUNT),
            },
            timeout=TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        raise DartError("실패: DART 서버 응답이 10초 안에 오지 않았습니다.") from None
    except requests.RequestException:
        raise DartError(
            "실패: DART 서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요."
        ) from None

    try:
        body = response.json()
    except ValueError:
        raise DartError(
            "실패: DART 응답을 해석하지 못했습니다.\n"
            f"HTTP 상태 코드: {response.status_code}"
        ) from None

    if not isinstance(body, dict):
        raise DartError("실패: DART 응답 형식이 예상과 다릅니다.")

    # 개발가이드 표에는 result 칸이 있지만, 실제 JSON은 최상위에 status가 있다.
    if "status" not in body and isinstance(body.get("result"), dict):
        body = body["result"]
    return body


def _status_of(body: dict) -> tuple[str, str]:
    status = str(body.get("status", "")).strip()
    message = str(body.get("message", "")).strip()
    return status, message


def _as_items(body: dict) -> list[Disclosure]:
    raw_list = body.get("list") or []
    if not isinstance(raw_list, list):
        raise DartError("실패: DART 응답에 공시 목록이 없습니다.")
    items: list[Disclosure] = []
    for row in raw_list:
        if not isinstance(row, dict):
            continue
        items.append(
            Disclosure(
                rcept_no=str(row.get("rcept_no") or ""),
                corp_name=str(row.get("corp_name") or ""),
                stock_code=str(row.get("stock_code") or "").strip(),
                report_nm=str(row.get("report_nm") or ""),
            )
        )
    return items


def fetch_disclosures_between(api_key: str, bgn_de: str, end_de: str) -> DisclosurePage:
    """시작일부터 종료일까지 접수된 공시를 가져온다. 최대 10페이지."""
    first = _request_page(api_key, bgn_de, end_de, 1)
    status, message = _status_of(first)
    if status == "013":
        return DisclosurePage(items=[], truncated=False, empty=True)
    if status != "000":
        raise DartError(
            "실패: DART가 조회를 거절했습니다.\n"
            f"status: {status}\n"
            f"message: {message or '알 수 없는 오류'}"
        )

    items = _as_items(first)
    try:
        total_page = int(first.get("total_page") or 1)
    except (TypeError, ValueError):
        total_page = 1
    if total_page < 1:
        total_page = 1

    last_page = min(total_page, MAX_PAGES)
    for page_no in range(2, last_page + 1):
        page = _request_page(api_key, bgn_de, end_de, page_no)
        page_status, page_message = _status_of(page)
        if page_status == "013":
            break
        if page_status != "000":
            raise DartError(
                "실패: DART가 다음 페이지 조회를 거절했습니다.\n"
                f"status: {page_status}\n"
                f"message: {page_message or '알 수 없는 오류'}"
            )
        items.extend(_as_items(page))

    return DisclosurePage(
        items=items,
        truncated=total_page > MAX_PAGES,
        empty=False,
    )


def fetch_today(api_key: str) -> DisclosurePage:
    """오늘(한국 시간) 접수된 공시를 가져온다."""
    today = today_yyyymmdd()
    return fetch_disclosures_between(api_key, today, today)
