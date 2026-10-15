"""감시 종목의 모든 공시와, 코스피·코스닥의 키워드 공시를 텔레그램으로 보낸다.

이미 보낸 접수번호는 sent_rcept_no.txt에 있으면 다시 보내지 않는다.
--baseline 이면 텔레그램으로 보내지 않고 기록만 한다.
"""

import argparse
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from dart_client import MAX_PAGES, DartError, _request_page, today_yyyymmdd
from main import ENV_PATH, load_dotenv

WATCHLIST_PATH = Path(__file__).resolve().parent / "watchlist.txt"
KEYWORDS_PATH = Path(__file__).resolve().parent / "keywords.txt"
EXCLUDE_PATH = Path(__file__).resolve().parent / "exclude_keywords.txt"
SENT_PATH = Path(__file__).resolve().parent / "sent_rcept_no.txt"
TIMEOUT_SECONDS = 15
KEEP_DAYS = 7
MAX_SENDS = 30
SEND_GAP_SECONDS = 1
MAX_429_ATTEMPTS = 3
MAX_RETRY_WAIT_SECONDS = 60
LISTED_MARKETS = {"Y", "K"}
MARKET_NAME = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타"}


class TelegramError(Exception):
    """사람이 읽을 수 있는 텔레그램 전송 오류. 토큰과 chat_id는 넣지 않는다."""

    def __init__(self, message: str, kind: str = "전송 거절") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class Notice:
    rcept_no: str
    corp_name: str
    stock_code: str
    report_nm: str
    corp_cls: str
    rcept_dt: str


def load_lines(path: Path, label: str) -> list[str]:
    """한 줄에 하나. 빈 줄과 # 주석은 무시한다."""
    if not path.is_file():
        print(f"실패: {label} 파일이 없습니다.")
        raise SystemExit(1)

    rows: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(line)
    return rows


def format_received_date(value: str) -> str:
    """접수일 YYYYMMDD를 YYYY-MM-DD로 바꾼다."""
    digits = value.strip()
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return digits


def build_message(notice: Notice) -> str:
    """텔레그램에 보낼 공시 한 건 문구를 만든다."""
    market = MARKET_NAME.get(notice.corp_cls, "기타")
    link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={notice.rcept_no}"
    return f"[{market}] {notice.corp_name} / {notice.report_nm}\n원문: {link}"


def load_sent(path: Path, today: date) -> list[tuple[str, str]]:
    """보낸 접수번호를 읽는다. 7일보다 오래된 줄은 버린다. 파일이 없으면 빈 목록."""
    if not path.is_file():
        return []

    cutoff = today - timedelta(days=KEEP_DAYS)
    kept: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or "," not in line:
            continue
        rcept_no, received = line.split(",", 1)
        rcept_no = rcept_no.strip()
        received = received.strip()
        try:
            received_date = datetime.strptime(received, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not rcept_no or received_date < cutoff:
            continue
        kept.append((rcept_no, received))
    return kept


def save_sent(path: Path, rows: list[tuple[str, str]]) -> None:
    """접수번호,접수일 형식으로 저장한다. 파일이 없으면 새로 만든다."""
    body = "".join(f"{rcept_no},{received}\n" for rcept_no, received in rows)
    path.write_text(body, encoding="utf-8")


def _retry_after_seconds(body: object) -> int:
    """429 응답의 대기 시간(초). 너무 크면 60초로 제한한다."""
    seconds = 5
    if isinstance(body, dict):
        params = body.get("parameters")
        if isinstance(params, dict):
            try:
                seconds = int(params.get("retry_after"))
            except (TypeError, ValueError):
                seconds = 5
    if seconds < 1:
        seconds = 1
    if seconds > MAX_RETRY_WAIT_SECONDS:
        seconds = 60
    return seconds


def send_telegram(token: str, chat_id: str, text: str) -> None:
    """sendMessage로 보낸다. 429면 대기 후 재시도한다. 주소는 출력하지 않는다."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(1, MAX_429_ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                json={"chat_id": chat_id, "text": text},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.Timeout:
            raise TelegramError(
                "실패: 텔레그램 서버 응답이 15초 안에 오지 않았습니다.",
                "시간 초과",
            ) from None
        except requests.RequestException:
            raise TelegramError(
                "실패: 텔레그램 서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.",
                "연결 실패",
            ) from None

        try:
            body = response.json()
        except ValueError:
            raise TelegramError(
                "실패: 텔레그램 응답을 해석하지 못했습니다.\n"
                f"HTTP 상태 코드: {response.status_code}",
                "응답 해석 실패",
            ) from None

        too_many = response.status_code == 429 or (
            isinstance(body, dict) and body.get("error_code") == 429
        )
        if too_many:
            if attempt == MAX_429_ATTEMPTS:
                raise TelegramError(
                    "실패: 텔레그램이 요청이 너무 많다고 거절했습니다. "
                    "이 공시는 기록하지 않고 다음 실행에서 다시 시도합니다.",
                    "요청 과다",
                )
            time.sleep(_retry_after_seconds(body))
            continue

        if response.ok and isinstance(body, dict) and body.get("ok") is True:
            return

        description = "알 수 없는 오류"
        if isinstance(body, dict):
            description = str(body.get("description") or description)
        if response.status_code == 403:
            kind = "권한 오류"
        elif response.status_code == 400:
            kind = "대화를 찾지 못함"
        else:
            kind = "전송 거절"
        raise TelegramError(
            "실패: 텔레그램이 메시지 전송을 거절했습니다.\n"
            f"HTTP 상태 코드: {response.status_code}\n"
            f"상세: {description}",
            kind,
        )


def _notices_from_body(body: dict) -> list[Notice]:
    raw_list = body.get("list") or []
    if not isinstance(raw_list, list):
        raise DartError("실패: DART 응답에 공시 목록이 없습니다.")
    notices: list[Notice] = []
    for row in raw_list:
        if not isinstance(row, dict):
            continue
        notices.append(
            Notice(
                rcept_no=str(row.get("rcept_no") or "").strip(),
                corp_name=str(row.get("corp_name") or "").strip(),
                stock_code=str(row.get("stock_code") or "").strip(),
                report_nm=str(row.get("report_nm") or "").strip(),
                corp_cls=str(row.get("corp_cls") or "").strip(),
                rcept_dt=str(row.get("rcept_dt") or "").strip(),
            )
        )
    return notices


def fetch_today_notices(api_key: str) -> list[Notice]:
    """오늘 공시를 가져온다. corp_cls와 rcept_dt를 함께 남긴다."""
    day = today_yyyymmdd()
    first = _request_page(api_key, day, day, 1)
    status = str(first.get("status", "")).strip()
    message = str(first.get("message", "")).strip()
    if status == "013":
        return []
    if status != "000":
        raise DartError(
            "실패: DART가 조회를 거절했습니다.\n"
            f"status: {status}\n"
            f"message: {message or '알 수 없는 오류'}"
        )

    notices = _notices_from_body(first)
    try:
        total_page = int(first.get("total_page") or 1)
    except (TypeError, ValueError):
        total_page = 1
    if total_page < 1:
        total_page = 1

    last_page = min(total_page, MAX_PAGES)
    for page_no in range(2, last_page + 1):
        page = _request_page(api_key, day, day, page_no)
        page_status = str(page.get("status", "")).strip()
        page_message = str(page.get("message", "")).strip()
        if page_status == "013":
            break
        if page_status != "000":
            raise DartError(
                "실패: DART가 다음 페이지 조회를 거절했습니다.\n"
                f"status: {page_status}\n"
                f"message: {page_message or '알 수 없는 오류'}"
            )
        notices.extend(_notices_from_body(page))
    return notices


def title_has_keyword(title: str, keywords: list[str]) -> bool:
    return any(keyword in title for keyword in keywords)


def is_selected(
    notice: Notice,
    watchlist: set[str],
    keywords: list[str],
    exclude_keywords: list[str],
) -> bool:
    """감시 종목은 모든 공시. 코스피·코스닥은 포함 키워드가 있고 제외 키워드가 없을 때만."""
    if notice.stock_code and notice.stock_code in watchlist:
        return True
    if notice.corp_cls not in LISTED_MARKETS or not notice.stock_code:
        return False
    if title_has_keyword(notice.report_nm, exclude_keywords):
        return False
    return title_has_keyword(notice.report_nm, keywords)


def choose_notices(
    notices: list[Notice],
    watchlist: set[str],
    keywords: list[str],
    exclude_keywords: list[str],
) -> list[Notice]:
    """조건에 맞는 공시를 고른다. 같은 접수번호는 한 번만."""
    chosen: list[Notice] = []
    seen: set[str] = set()
    for notice in notices:
        if not notice.rcept_no or notice.rcept_no in seen:
            continue
        if not is_selected(notice, watchlist, keywords, exclude_keywords):
            continue
        seen.add(notice.rcept_no)
        chosen.append(notice)
    return chosen


def excluded_titles(
    notices: list[Notice],
    watchlist: set[str],
    keywords: list[str],
    exclude_keywords: list[str],
) -> list[str]:
    """포함 키워드가 있지만 제외 키워드 때문에 빠진 제목. 감시 종목은 빼지 않는다."""
    titles: list[str] = []
    seen: set[str] = set()
    for notice in notices:
        if not notice.rcept_no or notice.rcept_no in seen:
            continue
        seen.add(notice.rcept_no)
        if notice.stock_code and notice.stock_code in watchlist:
            continue
        if notice.corp_cls not in LISTED_MARKETS or not notice.stock_code:
            continue
        if not title_has_keyword(notice.report_nm, keywords):
            continue
        if title_has_keyword(notice.report_nm, exclude_keywords):
            titles.append(notice.report_nm)
    return titles


def summary_line(
    sent_count: int,
    direct_count: int,
    channel_ok: int,
    channel_fail: int,
    skipped: int,
    carried: int,
) -> str:
    return (
        f"새 공시 {sent_count}건 전송"
        f"(1:1 {direct_count}건, 채널 {channel_ok}건, 채널 실패 {channel_fail}건), "
        f"이미 보낸 공시 {skipped}건 건너뜀, 이월 {carried}건"
    )


def remember(sent_rows: list[tuple[str, str]], sent_ids: set[str], notice: Notice) -> None:
    received = format_received_date(notice.rcept_dt)
    sent_rows.append((notice.rcept_no, received))
    sent_ids.add(notice.rcept_no)


def main() -> None:
    parser = argparse.ArgumentParser(description="감시 공시를 텔레그램으로 보냅니다.")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="오늘 조건에 맞는 공시를 보내지 않고 기록만 합니다.",
    )
    baseline = parser.parse_args().baseline

    load_dotenv(ENV_PATH)
    api_key = os.environ.get("DART_API_KEY", "").strip()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    missing = []
    if not api_key:
        missing.append("DART_API_KEY")
    if not baseline:
        if not token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat_id:
            missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print("실패: 환경변수가 없습니다:", ", ".join(missing))
        print(".env에 값을 넣은 뒤 다시 실행하세요.")
        raise SystemExit(1)

    today = datetime.strptime(today_yyyymmdd(), "%Y%m%d").date()
    sent_rows = load_sent(SENT_PATH, today)
    sent_ids = {rcept_no for rcept_no, _received in sent_rows}
    save_sent(SENT_PATH, sent_rows)

    watchlist = set(load_lines(WATCHLIST_PATH, "watchlist.txt"))
    keywords = load_lines(KEYWORDS_PATH, "keywords.txt")
    exclude_keywords = load_lines(EXCLUDE_PATH, "exclude_keywords.txt")

    try:
        notices = fetch_today_notices(api_key)
    except DartError as exc:
        print(exc)
        raise SystemExit(1)

    chosen = choose_notices(notices, watchlist, keywords, exclude_keywords)
    pending = [notice for notice in chosen if notice.rcept_no not in sent_ids]
    skipped = len(chosen) - len(pending)

    if baseline:
        for notice in pending:
            remember(sent_rows, sent_ids, notice)
        save_sent(SENT_PATH, sent_rows)
        kospi = sum(1 for notice in pending if notice.corp_cls == "Y")
        kosdaq = sum(1 for notice in pending if notice.corp_cls == "K")
        other = len(pending) - kospi - kosdaq
        print(
            f"기준선 기록 {len(pending)}건 "
            f"(코스피 {kospi}건, 코스닥 {kosdaq}건, 그 외 {other}건)"
        )
        for keyword in keywords:
            count = sum(1 for notice in pending if keyword in notice.report_nm)
            print(f"키워드 {keyword}: {count}건")
        blocked = excluded_titles(notices, watchlist, keywords, exclude_keywords)
        if blocked:
            print(f"제외어로 빠진 공시 {len(blocked)}건")
            for title in blocked:
                print(f"제외: {title}")
        else:
            print("제외어로 빠진 공시 없음")
        print(summary_line(0, 0, 0, 0, skipped, 0))
        return

    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    send_now = pending[:MAX_SENDS]
    carried = pending[MAX_SENDS:]
    sent_count = 0
    direct_count = 0
    channel_ok = 0
    channel_fail = 0
    for index, notice in enumerate(send_now, start=1):
        if index > 1:
            time.sleep(SEND_GAP_SECONDS)
        text = build_message(notice)
        try:
            send_telegram(token, chat_id, text)
        except TelegramError as exc:
            save_sent(SENT_PATH, sent_rows)
            left = (len(send_now) - sent_count) + len(carried)
            print(exc)
            print(f"실패: {index}번째 새 공시에서 전송을 멈췄습니다.")
            print(summary_line(sent_count, direct_count, channel_ok, channel_fail, skipped, left))
            raise SystemExit(1)
        remember(sent_rows, sent_ids, notice)
        sent_count += 1
        direct_count += 1
        save_sent(SENT_PATH, sent_rows)
        if not channel_id:
            continue
        time.sleep(SEND_GAP_SECONDS)
        try:
            send_telegram(token, channel_id, text)
        except TelegramError as exc:
            channel_fail += 1
            print(f"채널 실패: {notice.rcept_no} {exc.kind}")
            continue
        channel_ok += 1

    print(summary_line(sent_count, direct_count, channel_ok, channel_fail, skipped, len(carried)))


if __name__ == "__main__":
    main()
