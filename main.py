import os
from pathlib import Path

import requests

ENV_PATH = Path(__file__).resolve().parent / ".env"
MESSAGE_TEXT = "테스트 메시지"


def load_dotenv(path: Path) -> None:
    """로컬 .env 파일을 읽어 환경변수로 넣는다.

    이미 설정된 환경변수는 바꾸지 않는다.
    빈 줄과 #으로 시작하는 줄은 무시한다.
    """
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    load_dotenv(ENV_PATH)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    missing = []
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print("실패: 환경변수가 없습니다:", ", ".join(missing))
        print(".env.example을 복사해 .env를 만들고 값을 채운 뒤 다시 실행하세요.")
        raise SystemExit(1)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": MESSAGE_TEXT},
            timeout=15,
        )
    except requests.RequestException as exc:
        print("실패: 텔레그램 서버에 연결하지 못했습니다.")
        print(f"상세: {exc}")
        raise SystemExit(1)

    try:
        body = response.json()
    except ValueError:
        print("실패: 텔레그램 응답을 해석하지 못했습니다.")
        print(f"HTTP 상태 코드: {response.status_code}")
        raise SystemExit(1)

    if response.ok and body.get("ok") is True:
        print("성공: 테스트 메시지를 보냈습니다.")
        return

    description = body.get("description", "알 수 없는 오류")
    print("실패: 텔레그램이 메시지 전송을 거절했습니다.")
    print(f"HTTP 상태 코드: {response.status_code}")
    print(f"상세: {description}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
