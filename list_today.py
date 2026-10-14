"""오늘 접수된 DART 공시 목록을 터미널에 출력한다."""

import os

from dart_client import DartError, fetch_today
from main import ENV_PATH, load_dotenv

SK_HYNIX_CODE = "000660"


def main() -> None:
    load_dotenv(ENV_PATH)
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        print("실패: DART API 키가 없습니다.")
        print(".env에 DART_API_KEY를 넣은 뒤 다시 실행하세요.")
        raise SystemExit(1)

    try:
        result = fetch_today(api_key)
    except DartError as exc:
        print(exc)
        raise SystemExit(1)

    if result.empty:
        print("오늘 공시 없음")

    for item in result.items:
        print(
            f"{item.rcept_no} | {item.corp_name} | {item.stock_code} | {item.report_nm}"
        )

    if result.truncated:
        print("안내: 공시가 많아 최대 10페이지만 가져왔습니다.")

    hynix_count = sum(1 for item in result.items if item.stock_code == SK_HYNIX_CODE)
    print(f"총 {len(result.items)}건")
    print(f"종목코드 000660 공시 {hynix_count}건")


if __name__ == "__main__":
    main()
