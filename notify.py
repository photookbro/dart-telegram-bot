"""오늘 공시 중 감시 종목만 텔레그램으로 보낸다."""

import os
from pathlib import Path

import requests

from dart_client import DartError, fetch_today, today_yyyymmdd
from main import ENV_PATH, load_dotenv

WATCHLIST_PATH = Path(__file__).resolve().parent / "watchlist.txt"
TIMEOUT_SECONDS = 15


class TelegramError(Exception):
    """사람이 읽을 수 있는 텔레그램 전송 오류. 토큰과 chat_id는 넣지 않는다."""


def load_watchlist(path: Path) -> set[str]:
    """한 줄에 종목코드 하나. 빈 줄과 # 주석은 무시한다."""
    if not path.is_file():
        print("실패: watchlist.txt 파일이 없습니다.")
        raise SystemExit(1)

    codes: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        codes.add(line)
    return codes


def build_message(corp_name: str, report_nm: str, rcept_no: str, yyyymmdd: str) -> str:
    """텔레그램에 보낼 공시 한 건 문구를 만든다."""
    received = f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
    return f"[{corp_name}] {report_nm}\n접수일: {received}\n원문: {link}"


def send_telegram(token: str, chat_id: str, text: str) -> None:
    """main.py와 같이 sendMessage로 보낸다. 주소와 오류 원문은 출력하지 않는다."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        raise TelegramError(
            "실패: 텔레그램 서버 응답이 15초 안에 오지 않았습니다."
        ) from None
    except requests.RequestException:
        raise TelegramError(
            "실패: 텔레그램 서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요."
        ) from None

    try:
        body = response.json()
    except ValueError:
        raise TelegramError(
            "실패: 텔레그램 응답을 해석하지 못했습니다.\n"
            f"HTTP 상태 코드: {response.status_code}"
        ) from None

    if response.ok and body.get("ok") is True:
        return

    description = "알 수 없는 오류"
    if isinstance(body, dict):
        description = str(body.get("description") or description)
    raise TelegramError(
        "실패: 텔레그램이 메시지 전송을 거절했습니다.\n"
        f"HTTP 상태 코드: {response.status_code}\n"
        f"상세: {description}"
    )


def main() -> None:
    load_dotenv(ENV_PATH)

    api_key = os.environ.get("DART_API_KEY", "").strip()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    missing = []
    if not api_key:
        missing.append("DART_API_KEY")
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print("실패: 환경변수가 없습니다:", ", ".join(missing))
        print(".env에 값을 넣은 뒤 다시 실행하세요.")
        raise SystemExit(1)

    watchlist = load_watchlist(WATCHLIST_PATH)
    if not watchlist:
        print("감시 종목 공시 없음")
        return

    try:
        result = fetch_today(api_key)
    except DartError as exc:
        print(exc)
        raise SystemExit(1)

    matched = [item for item in result.items if item.stock_code in watchlist]
    if not matched:
        print("감시 종목 공시 없음")
        return

    received_on = today_yyyymmdd()
    for index, item in enumerate(matched, start=1):
        text = build_message(
            item.corp_name.strip(),
            item.report_nm.strip(),
            item.rcept_no.strip(),
            received_on,
        )
        try:
            send_telegram(token, chat_id, text)
        except TelegramError as exc:
            print(exc)
            print(f"실패: {index}번째 공시에서 전송을 멈췄습니다.")
            raise SystemExit(1)

    print(f"{len(matched)}건 전송했습니다")


if __name__ == "__main__":
    main()
