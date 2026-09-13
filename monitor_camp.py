#!/usr/bin/env python3
"""
달서별빛캠프 (camp.xticket.kr) 오토캠핑장 취소표(빈자리) 감시 스크립트
------------------------------------------------------------------
지정한 날짜 + 오토캠핑장으로 실제 클릭 조작을 재현한 뒤, 사이트 지도에
표시되는 자리 아이콘의 alt 텍스트("예약완료" 포함 여부)로 상태를 판별합니다.

사용법
  1) 아래 "설정"만 본인 상황에 맞게 바꾸세요.
  2) pip install playwright
     playwright install chromium
  3) python monitor_camp.py            (1회 실행 테스트)
  4) 정상 동작 확인되면, OS 스케줄러(cron / 작업 스케줄러)에
     "python monitor_camp.py"를 원하는 주기(예: 5분)로 등록하세요.

알림 방법은 기본으로 ntfy.sh(무료, 가입 불필요)를 사용합니다.
  - 휴대폰에 ntfy 앱(iOS/Android) 설치 → NTFY_TOPIC과 동일한 토픽 구독
  - 또는 브라우저에서 https://ntfy.sh/<토픽이름> 열어두면 웹으로도 알림 확인 가능
이메일로 받고 싶다면 아래 send_email() 함수의 주석을 참고해서 바꿔 쓰세요.
"""

import datetime
import json
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# ----------------------------- 설정 -----------------------------------
SHOP_URL = (
    "https://camp.xticket.kr/web/main"
    "?shopEncode=f27ad3485cf140b64341e2cc975c376d14561a12aca07349ae393d97379882c7"
)
TARGET_DAY = "26"          # 체크인 날짜(일) - 달력에 보이는 "26" 클릭
FACILITY_RADIO_ID = "오토캠핑장"   # 카라반 / 오토캠핑장 / 숲속캠핑장 / 데크캠핑장
NTFY_TOPIC = "cmsoon123-camp0926-9f3a1"  # 공개 저장소이므로 남이 추측 못 할 문자열로 변경 권장
CHROMIUM_PATH = None       # 보통 None으로 두면 playwright가 알아서 찾습니다.
# ------------------------------------------------------------------------


def notify(message: str):
    """ntfy.sh로 푸시 알림 전송 (가입/토큰 불필요, 토픽 이름만 비밀로 유지)."""
    try:
        req = urllib.request.Request(
            url=f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": "캠핑장 취소표 발생!".encode("utf-8")},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[알림 전송 실패] {e}")

    # --- 이메일로 받고 싶다면 아래 예시처럼 바꿔서 notify() 안에서 호출하세요 ---
    # import smtplib
    # from email.mime.text import MIMEText
    # msg = MIMEText(message)
    # msg["Subject"] = "캠핑장 취소표 발생!"
    # msg["From"] = "you@gmail.com"
    # msg["To"] = "you@gmail.com"
    # with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
    #     s.login("you@gmail.com", "앱 비밀번호(App Password)")
    #     s.send_message(msg)


def check_once() -> dict:
    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if CHROMIUM_PATH:
            launch_kwargs["executable_path"] = CHROMIUM_PATH
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page()
        page.goto(SHOP_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)

        # 1) 시설 선택: 오토캠핑장 라디오 클릭 (id가 한글 그대로라 CSS #id 대신 속성 선택자 사용)
        page.click(f"input[id='{FACILITY_RADIO_ID}']")
        page.wait_for_timeout(1200)

        # 2) 날짜 선택: 달력에서 정확히 TARGET_DAY 텍스트인 <a> 클릭
        #    (월이 안 맞으면 다음달 화살표를 눌러가며 찾음 - 최대 6개월)
        clicked = False
        for _ in range(6):
            day_link = page.locator("a", has_text=re.compile(rf"^\s*{TARGET_DAY}\s*$"))
            if day_link.count() > 0:
                day_link.first.click()
                clicked = True
                break
            next_btn = page.locator("a[href*='goNextMonth']")
            if next_btn.count() == 0:
                break
            next_btn.first.click()
            page.wait_for_timeout(500)

        if not clicked:
            browser.close()
            return {"error": f"{TARGET_DAY}일 달력 링크를 찾지 못했습니다."}

        page.wait_for_timeout(1500)

        # 3) 상태 읽기: 오토캠핑 자리 아이콘들의 alt 텍스트 확인
        #    (이 환경에서 eval_on_selector_all / evaluate 둘 다 내부 오류나 빈 값을
        #     내는 경우가 있어, 가장 기본적인 Locator API로 하나씩 읽음)
        icon_locator = page.locator("img[alt*='오토캠핑']")
        icon_count = icon_locator.count()
        alts = [icon_locator.nth(i).get_attribute("alt") for i in range(icon_count)]
        alts = [a for a in alts if a]

        browser.close()

        if not alts:
            return {
                "error": (
                    "오토캠핑 자리 아이콘을 하나도 찾지 못했습니다 "
                    "(날짜/시설 선택이 제대로 반영되지 않았을 수 있습니다)."
                )
            }

        sold = [a for a in alts if "예약완료" in a]
        open_sites = [a for a in alts if a and "예약완료" not in a]

        return {
            "total": len(alts),
            "sold_out": len(sold),
            "open_sites": open_sites,
        }


LOG_FILE = Path(__file__).with_name("camp_monitor_log.txt")


def write_log(line: str):
    """작업 스케줄러로 백그라운드 실행될 때는 화면에 출력이 안 보이므로,
    같은 폴더의 camp_monitor_log.txt에 매번 결과를 한 줄씩 남깁니다.
    이 파일을 언제든 열어보면 최근 체크 결과와 이력을 확인할 수 있어요."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")
    except Exception as e:
        print(f"[로그 기록 실패] {e}")


def main():
    result = check_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("error"):
        # 페이지 구조가 바뀌었거나 일시적 오류 - 알림은 보내지 않고 로그만 남김
        write_log(f"오류: {result['error']}")
        return

    write_log(
        f"total={result['total']} sold_out={result['sold_out']} "
        f"open_sites={result['open_sites']}"
    )

    if result["open_sites"]:
        msg = "오토캠핑장 자리가 열렸습니다!\n" + "\n".join(result["open_sites"])
        notify(msg)
        print(">>> 빈자리 발견! 알림을 보냈습니다.")
    else:
        print("아직 전부 예약완료 상태입니다.")


if __name__ == "__main__":
    sys.exit(main())
