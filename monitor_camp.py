#!/usr/bin/env python3
"""
달서별빛캠프 (camp.xticket.kr) 오토/데크캠핑장 취소표(빈자리) 감시 스크립트
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
# 감시할 시설: (라디오 버튼 id, 자리 아이콘 alt에 들어가는 키워드)
#   선택지: 카라반 / 오토캠핑장 / 숲속캠핑장 / 데크캠핑장
FACILITIES = [
    ("오토캠핑장", "오토캠핑"),
    ("데크캠핑장", "데크"),
]
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


def dismiss_notice(page):
    """첫 화면에 뜨는 공지사항 팝업(notice_bg 반투명 배경)이 클릭을 가로막으므로 치운다."""
    # 1) '닫기' 류 버튼이 있으면 눌러본다
    for sel in [
        "[class*='notice'] a:has-text('닫기')",
        "[class*='notice'] button:has-text('닫기')",
        "[class*='notice'] a:has-text('오늘')",
        "[class*='notice'] [class*='close']",
    ]:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=2000, force=True)
                page.wait_for_timeout(500)
        except Exception:
            pass
    # 2) 그래도 남아 있으면 팝업/배경 요소를 DOM에서 제거
    try:
        page.evaluate(
            """() => document.querySelectorAll(
                   ".notice_bg, [class*='notice_pop'], [class*='notice_layer'], [id*='notice']"
               ).forEach(el => el.remove())"""
        )
    except Exception as e:
        print(f"[팝업 제거 실패, 계속 진행] {e}")
    page.wait_for_timeout(300)


def check_facility(page, radio_id: str, alt_keyword: str) -> dict:
    page.goto(SHOP_URL, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1000)
    dismiss_notice(page)

    # 1) 시설 선택 (id가 한글이라 속성 선택자 사용). force=True: 혹시 남은 오버레이 무시
    page.click(f"input[id='{radio_id}']", force=True, timeout=10000)
    page.wait_for_timeout(1200)
    dismiss_notice(page)

    # 2) 날짜 선택 (월이 안 맞으면 다음달로 넘기며 최대 6개월 탐색)
    clicked = False
    for _ in range(6):
        day_link = page.locator("a", has_text=re.compile(rf"^\s*{TARGET_DAY}\s*$"))
        if day_link.count() > 0:
            day_link.first.click(force=True, timeout=10000)
            clicked = True
            break
        next_btn = page.locator("a[href*='goNextMonth']")
        if next_btn.count() == 0:
            break
        next_btn.first.click(force=True)
        page.wait_for_timeout(500)
    if not clicked:
        return {"error": f"{TARGET_DAY}일 달력 링크를 찾지 못했습니다."}

    page.wait_for_timeout(1500)

    # 3) 자리 아이콘 alt 텍스트로 상태 판별
    icon_locator = page.locator(f"img[alt*='{alt_keyword}']")
    alts = [icon_locator.nth(i).get_attribute("alt") for i in range(icon_locator.count())]
    alts = [a for a in alts if a]
    if not alts:
        return {"error": f"'{alt_keyword}' 자리 아이콘을 찾지 못했습니다 (선택이 반영되지 않았을 수 있음)."}

    sold = [a for a in alts if "예약완료" in a]
    open_sites = [a for a in alts if "예약완료" not in a]
    return {"total": len(alts), "sold_out": len(sold), "open_sites": open_sites}


def check_once() -> dict:
    results = {}
    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if CHROMIUM_PATH:
            launch_kwargs["executable_path"] = CHROMIUM_PATH
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page()
        for radio_id, alt_keyword in FACILITIES:
            try:
                results[radio_id] = check_facility(page, radio_id, alt_keyword)
            except Exception as e:
                results[radio_id] = {"error": f"{type(e).__name__}: {str(e).splitlines()[0]}"}
                try:
                    page.screenshot(path=f"error_{radio_id}.png", full_page=True)
                except Exception:
                    pass
        browser.close()
    return results


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
    results = check_once()
    print(json.dumps(results, ensure_ascii=False, indent=2))

    found_lines = []
    errors = []
    for name, r in results.items():
        if r.get("error"):
            errors.append(f"{name}: {r['error']}")
            write_log(f"{name} 오류: {r['error']}")
            continue
        write_log(f"{name} total={r['total']} sold_out={r['sold_out']} open_sites={r['open_sites']}")
        if r["open_sites"]:
            found_lines.append(f"[{name}]")
            found_lines.extend(r["open_sites"])

    if found_lines:
        notify("9/26 캠핑장 자리가 열렸습니다!\n" + "\n".join(found_lines))
        print(">>> 빈자리 발견! 알림을 보냈습니다.")
    elif not errors:
        print("아직 전부 예약완료 상태입니다.")

    # 모든 시설 확인이 실패했을 때만 실패로 종료 (GitHub Actions에서 빨간불로 표시)
    return 1 if len(errors) == len(results) else 0


if __name__ == "__main__":
    sys.exit(main())
