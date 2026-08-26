import os
import re
import json
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
CRAIGSLIST_SITES = ["tampa", "lakeland", "sarasota", "orlando"]
FB_LOCATION = "tampa"  # Tampa, FL Facebook Marketplace region

# Match exact working GitHub Actions secret names
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")


def check_craigslist():
    """Scrapes local Florida Craigslist sites for Power Plate listings."""
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
                            price_elem.text.strip() if price_elem else "Price N/A"
                        )

                        # Filter for relevant vibration platform titles
                        if any(
                            q in title.lower()
                            for q in [
                                "power plate",
                                "powerplate",
                                "my7",
                                "my5",
                                "my3",
                            ]
                        ):
                            matches.append(
                                {
                                    "source": f"Craigslist ({site.title()})",
                                    "title": title,
                                    "price": price,
                                    "link": link,
                                }
                            )
            except Exception as e:
                print(f"Error checking Craigslist {site}: {e}")

    return matches

def check_facebook_marketplace():
    """Scrapes Facebook Marketplace in Tampa using Playwright and storage_state.json."""
    matches = []
    session_file = "storage_state.json"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "viewport": {'width': 1280, 'height': 800}
        }

        # Load existing session and sanitize cookies in memory
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
                print("Loaded and sanitized storage_state.json for PowerPlate monitor.")
            except Exception as e:
                print(f"Warning: Failed to load storage_state.json: {e}")

        context = browser.new_context(**context_args)
        page = context.new_page()

        for query in ["Power Plate", "PowerPlate", "my7 Power Plate"]:
            url = f"https://www.facebook.com/marketplace/{FB_LOCATION}/search/?query={query.replace(' ', '%20')}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(4)  # Allow dynamic listings to render

                # Extract listing grid cards
                cards = page.locator('a[href*="/marketplace/item/"]').all()

                for card in cards[:10]:  # Inspect top 10 results per query
                    text = card.inner_text()
                    href = card.get_attribute("href")
                    full_link = (
                        f"https://www.facebook.com{href.split('?')[0]}"
                        if href
                        else "N/A"
                    )

                    lines = [line.strip() for line in text.split("\n") if line.strip()]
                    if lines:
                        # FB cards typically format as: Price, Title, Location
                        price = lines[0] if "$" in lines[0] else "Price N/A"
                        title = lines[1] if len(lines) > 1 else lines[0]

                        if any(
                            q in title.lower()
                            for q in [
                                "power plate",
                                "powerplate",
                                "vibration",
                                "my7",
                                "my5",
                            ]
                        ):
                            matches.append(
                                {
                                    "source": "Facebook Marketplace (Tampa)",
                                    "title": title,
                                    "price": price,
                                    "link": full_link,
                                }
                            )
            except Exception as e:
                print(f"Error scraping Facebook Marketplace for '{query}': {e}")

        browser.close()

    # Deduplicate matches by link
    seen_links = set()
    unique_matches = []
    for m in matches:
        if m["link"] not in seen_links:
            seen_links.add(m["link"])
            unique_matches.append(m)

    return unique_matches


def send_email_alert(matches):
    """Sends HTML email notification for matched listings."""
    if not matches or not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("No new matches or missing email environment variables.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"💪 Power Plate Alert: {len(matches)} Listing(s) Found in Tampa!"
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

    print("Checking Facebook Marketplace for Power Plates...")
    fb_results = check_facebook_marketplace()

    all_results = cl_results + fb_results
    print(f"\nScan Complete: Found {len(all_results)} match(es).")

    for r in all_results:
        print(f" - [{r['source']}] {r['title']} ({r['price']}) -> {r['link']}")

    if all_results:
        send_email_alert(all_results)