import json
import os
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
CRAIGSLIST_SITES = ["tampa", "lakeland", "sarasota", "orlando"]
FB_LOCATION = "tampa"  # Tampa, FL Facebook Marketplace region
NEXTDOOR_SEARCH_URL = (
    "https://nextdoor.com/for_sale_and_free/?query=power%20plate"
)

# Secrets matching GitHub Actions env mappings
EMAIL_SENDER = os.environ.get("EMAIL_SENDER") or os.environ.get("SENDER_EMAIL")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD") or os.environ.get(
    "SENDER_PASSWORD"
)
EMAIL_RECEIVER = (
    os.environ.get("EMAIL_RECEIVER")
    or os.environ.get("RECEIVER_EMAIL")
    or os.environ.get("RECIPIENT_EMAIL")
)
NEXTDOOR_SESSION_ID = os.environ.get("NEXTDOOR_SESSION_ID")

# Keywords for validating relevancy
TARGET_KEYWORDS = [
    "power plate",
    "powerplate",
    "vibration plate",
    "vibration platform",
    "my7",
    "my5",
    "my3",
    "personal power plate",
]


def is_valid_power_plate(title):
    """Filters out irrelevant items and ensures target keywords are present."""
    title_clean = title.lower()
    return any(keyword in title_clean for keyword in TARGET_KEYWORDS)


def check_craigslist():
    """Scrapes local Florida Craigslist sites for Power Plate listings using BeautifulSoup."""
    matches = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for site in CRAIGSLIST_SITES:
        for query in ["power plate", "vibration plate"]:
            url = f"https://{site}.craigslist.org/search/sss?query={query.replace(' ', '+')}"
            try:
                res = requests.get(url, headers=headers, timeout=15)
                if res.status_code != 200:
                    continue
                soup = BeautifulSoup(res.text, "html.parser")
                listings = soup.find_all("li", class_="cl-search-result")

                for item in listings:
                    title_elem = item.find("a", class_="titling")
                    price_elem = item.find("span", class_="priceinfo")

                    if title_elem:
                        title = title_elem.text.strip()
                        link = title_elem.get("href")
                        price = (
                            price_elem.text.strip()
                            if price_elem
                            else "Price N/A"
                        )

                        if is_valid_power_plate(title):
                            matches.append({
                                "source": f"Craigslist ({site.title()})",
                                "title": title,
                                "price": price,
                                "link": link,
                            })
            except Exception as e:
                print(f"Error checking Craigslist {site}: {e}")

    return matches


def check_playwright_sources():
    """Runs Playwright to scrape both Facebook Marketplace and Nextdoor in a single browser lifecycle."""
    matches = []
    session_file = "storage_state.json"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context_args = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 800},
        }

        # Load sanitized FB storage state if available
        if os.path.exists(session_file):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)

                for cookie in state_data.get("cookies", []):
                    exp = cookie.get("expires")
                    if exp is not None and float(exp) > 0:
                        exp_float = float(exp)
                        if exp_float > 32503680000:
                            exp_float = exp_float / 1000.0
                        cookie["expires"] = int(exp_float)
                    else:
                        cookie["expires"] = -1

                    cookie["secure"] = bool(cookie.get("secure"))
                    cookie["httpOnly"] = bool(cookie.get("httpOnly"))

                context_args["storage_state"] = state_data
                print("Loaded storage_state.json for Facebook.")
            except Exception as e:
                print(f"Warning: Failed to load storage_state.json: {e}")

        context = browser.new_context(**context_args)

        # --- 1. SCRAPE FACEBOOK MARKETPLACE ---
        print("Checking Facebook Marketplace for Power Plates...")
        fb_page = context.new_page()
        for query in ["Power Plate", "PowerPlate", "my7 Power Plate"]:
            url = f"https://www.facebook.com/marketplace/{FB_LOCATION}/search/?query={query.replace(' ', '%20')}"
            try:
                fb_page.goto(
                    url, wait_until="domcontentloaded", timeout=30000
                )
                time.sleep(4)  # Allow JS cards to load

                cards = fb_page.locator('a[href*="/marketplace/item/"]').all()
                for card in cards[:10]:
                    text = card.inner_text()
                    href = card.get_attribute("href")
                    full_link = (
                        f"https://www.facebook.com{href.split('?')[0]}"
                        if href
                        else "N/A"
                    )

                    lines = [
                        line.strip()
                        for line in text.split("\n")
                        if line.strip()
                    ]
                    if lines:
                        price = lines[0] if "$" in lines[0] else "Price N/A"
                        title = lines[1] if len(lines) > 1 else lines[0]

                        if is_valid_power_plate(title):
                            matches.append({
                                "source": "Facebook Marketplace (Tampa)",
                                "title": title,
                                "price": price,
                                "link": full_link,
                            })
            except Exception as e:
                print(
                    f"Error scraping Facebook Marketplace for '{query}': {e}"
                )
        fb_page.close()

        # --- 2. SCRAPE NEXTDOOR ---
        if NEXTDOOR_SESSION_ID:
            print("Checking Nextdoor For Sale & Free...")
            try:
                nd_page = context.new_page()
                # Attach ndbr_at session token
                nd_page.set_extra_http_headers({
                    "Cookie": f"ndbr_at={NEXTDOOR_SESSION_ID.strip()}"
                })

                nd_page.goto(
                    NEXTDOOR_SEARCH_URL,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                time.sleep(3)

                # Scroll to load dynamic elements
                for _ in range(2):
                    nd_page.evaluate("window.scrollBy(0, 1000);")
                    time.sleep(2)

                nd_cards = nd_page.locator(
                    'a[href*="/for_sale_and_free/"], a[href*="/post/"]'
                ).all()
                for card in nd_cards:
                    try:
                        href = card.get_attribute("href")
                        text = card.inner_text().strip()
                        lines = [
                            line.strip()
                            for line in text.split("\n")
                            if line.strip()
                        ]

                        if not href or not lines:
                            continue

                        price = next((l for l in lines if "$" in l), "Price N/A")
                        title = (
                            lines[0]
                            if lines[0] != price
                            else (lines[1] if len(lines) > 1 else "Nextdoor Item")
                        )

                        if is_valid_power_plate(title):
                            clean_link = (
                                href
                                if href.startswith("http")
                                else f"https://nextdoor.com{href.split('?')[0]}"
                            )
                            matches.append({
                                "source": "Nextdoor",
                                "title": title,
                                "price": price,
                                "link": clean_link,
                            })
                    except Exception:
                        continue
                nd_page.close()
            except Exception as e:
                print(f"Error scraping Nextdoor: {e}")
        else:
            print("NEXTDOOR_SESSION_ID missing. Skipping Nextdoor.")

        browser.close()

    # Deduplicate matches by link
    seen_links = set()
    unique_matches = []
    for m in matches:
        if m["link"] not in seen_links and m["link"] != "N/A":
            seen_links.add(m["link"])
            unique_matches.append(m)

    return unique_matches


def send_email_alert(matches):
    """Sends HTML email notification for matched listings."""
    if not matches:
        print("No matches to email.")
        return

    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email configuration missing. Skipping email dispatch.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"💪 Power Plate Alert: {len(matches)} Listing(s) Found!"
    )
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    html = "<h2>Power Plate Listings Found</h2><ul>"
    for m in matches:
        html += f"<li><b>[{m['source']}]</b> <a href='{m['link']}'>{m['title']}</a> - <b>{m['price']}</b></li>"
    html += "</ul>"

    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("Alert email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")


if __name__ == "__main__":
    print("Checking Craigslist for Power Plates...")
    cl_results = check_craigslist()

    playwright_results = check_playwright_sources()

    all_results = cl_results + playwright_results
    print(f"\nScan Complete: Found {len(all_results)} match(es).")

    for r in all_results:
        print(f" - [{r['source']}] {r['title']} ({r['price']}) -> {r['link']}")

    if all_results:
        send_email_alert(all_results)