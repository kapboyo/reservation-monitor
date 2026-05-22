import os
import re
import random
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

URL = "https://dshinsei.e-kanagawa.lg.jp/140007-u/reserve/offerList_detail?tempSeq=50909&accessFrom=offerList"

TARGET_ROWS = [
    "普通車ＡＭ",
    "普通車ＰＭ"
]

END_DATE = datetime(2026, 7, 28)


def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def send_discord(message):
    webhook_url = os.getenv(
        "DISCORD_WEBHOOK_URL"
    )

    if not webhook_url:
        log("Missing Discord webhook URL")
        return

    try:
        response = requests.post(
            webhook_url,
            json={
                "content": message
            },
            timeout=15
        )

        if response.status_code in [200, 204]:
            log("Discord notification sent")

        else:
            log(
                f"Discord failed: "
                f"{response.status_code}"
            )

    except Exception as e:
        log(f"Discord error: {e}")


def random_sleep(a=2, b=5):
    time.sleep(random.uniform(a, b))


def page_has_reservation_table(page):
    try:
        return (
            page.locator("#TBL").count() > 0
            and "普通車ＡＭ" in page.content()
        )
    except:
        return False


def extract_dates(page):
    headers = page.locator("#TBL tr#height_headday td")

    dates = []

    for i in range(headers.count()):
        raw_text = headers.nth(i).inner_text().strip()

        match = re.search(r"(\d{2})/(\d{2})", raw_text)

        if not match:
            dates.append(None)
            continue

        month = match.group(1)
        day = match.group(2)

        try:
            parsed = datetime.strptime(
                f"2026/{month}/{day}",
                "%Y/%m/%d"
            )

            dates.append(parsed)

        except Exception as e:
            log(f"Date parse failed: {e}")
            dates.append(None)

    return dates


def check_slots(page):
    found = []

    dates = extract_dates(page)

    valid_dates = [d for d in dates if d]

    if not valid_dates:
        log("No valid dates found")
        return found, True

    log(
        f"Scanning week: "
        f"{valid_dates[0].strftime('%Y-%m-%d')} "
        f"to "
        f"{valid_dates[-1].strftime('%Y-%m-%d')}"
    )

    # STOP ONCE ENTIRE PAGE EXCEEDS END DATE
    if all(d > END_DATE for d in valid_dates):
        log("Reached END_DATE")
        return found, True

    for row_name in TARGET_ROWS:
        log(f"Checking row: {row_name}")

        row = page.locator(
            f'tr[id="height_auto_{row_name}"]'
        )

        if row.count() == 0:
            log(f"Row missing: {row_name}")
            continue

        cells = row.locator("td")

        for i in range(
            min(cells.count(), len(dates))
        ):
            date = dates[i]

            if not date:
                continue

            if date > END_DATE:
                continue

            cell = cells.nth(i)

            cell_html = cell.inner_html()

            available = (
                'aria-label="予約可能"' in cell_html
                or '予約可能' in cell_html
                or '○' in cell.inner_text()
            )

            log(
                f"{row_name} "
                f"{date.strftime('%Y-%m-%d')} "
                f"available={available}"
            )

            if available:
                found.append({
                    "row": row_name,
                    "date": date.strftime("%Y-%m-%d")
                })

    return found, False


def main():
    page = None
    browser = None

    try:
        log("Starting browser")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                slow_mo=random.randint(20, 60)
            )

            context = browser.new_context(
                viewport={
                    "width": 1400,
                    "height": 1200
                },
                locale="ja-JP"
            )

            page = context.new_page()

            log("Opening reservation page")

            page.goto(
                URL,
                timeout=60000,
                wait_until="domcontentloaded"
            )

            random_sleep()

            checkbox = page.locator("#reserveCaution")

            if checkbox.count() > 0:
                page.evaluate("""
                    () => {
                        const checkbox =
                            document.querySelector(
                                '#reserveCaution'
                            );

                        if (checkbox) {
                            checkbox.checked = true;

                            checkbox.dispatchEvent(
                                new Event(
                                    'change',
                                    { bubbles: true }
                                )
                            );
                        }
                    }
                """)

                log("Accepted caution checkbox")

            random_sleep()

            # RATE LIMIT / BAD PAGE CHECK
            if not page_has_reservation_table(page):
                log(
                    "Reservation table missing "
                    "- likely timeout/rate limit"
                )

                send_discord(
                    "⚠️ Reservation table missing "
                    "- possible rate limit"
                )

                browser.close()
                return

            log("Starting reservation scan")

            week_counter = 0
            all_found_slots = []

            while True:
                week_counter += 1

                if week_counter > 20:
                    log(
                        "Emergency stop "
                        "(too many weeks)"
                    )
                    break

                log(f"Checking week #{week_counter}")

                slots, should_stop = check_slots(page)

                all_found_slots.extend(slots)

                if should_stop:
                    break

                next_button = page.locator(
                    'input[value="2週後＞"]'
                )

                if next_button.count() == 0:
                    log("Next button missing")
                    break

                if next_button.is_disabled():
                    log("Next button disabled")
                    break

                log("Moving to next week")

                try:
                    next_button.click(
                        timeout=10000,
                        no_wait_after=True
                    )

                except Exception as e:
                    log(
                        f"Next button click failed: "
                        f"{e}"
                    )
                    break

                page.wait_for_timeout(
                    random.randint(4000, 7000)
                )

                random_sleep(2, 4)

            # REMOVE DUPLICATES
            unique_slots = []

            seen = set()

            for slot in all_found_slots:
                key = (
                    f"{slot['date']}_"
                    f"{slot['row']}"
                )

                if key not in seen:
                    seen.add(key)
                    unique_slots.append(slot)

            all_found_slots = unique_slots

            if all_found_slots:
                body = "\n".join([
                    f"{s['date']} - {s['row']}"
                    for s in all_found_slots
                ])

                send_discord(
                    f"🚗 Driving Test Slot Found\n\n"
                    f"{body}\n\n"
                    f"Reservation Page:\n{URL}"
                )

                log(
                    "Availability notification sent"
                )

            else:
                log("No availability found")

            browser.close()

    except KeyboardInterrupt:
        log("Script stopped by user")

        try:
            if browser:
                browser.close()
        except:
            pass

    except Exception as e:
        log(f"ERROR: {e}")

        send_discord(
            f"❌ Reservation Monitor Error\n\n{str(e)}"
        )

        try:
            if browser:
                browser.close()
        except:
            pass


if __name__ == "__main__":
    main()
