import os
import sys
import json
import csv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Configuration
FB_MARKETPLACE_URL = "https://www.facebook.com/marketplace/search?query=golf%20cart"

# Email Configuration from environment variables
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

def load_seen_ids(filename="seen_ids.json"):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_ids(seen_ids, filename="seen_ids.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, indent=2)

def send_email_notification(new_matches):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAIL:
        print("Email configuration missing. Skipping email dispatch.")
        return

    subject = f"Golf Cart Alert: {len(new_matches)} New Listing(s) Found!"
    
    body = "<h2>New Golf Cart Listings Found:</h2><ul>"
    for item in new_matches:
        body += f"<li><strong>{item['title']}</strong> - {item['price']}<br><a href='{item['link']}'>View Listing</a></li><br>"
    body += "</ul>"

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print("Email alert sent successfully!")
    except Exception as e:
        print(f"Failed to send email alert: {e}")

def run_scraper():
    seen_ids = load_seen_ids()
    new_matches = []

    print("Launching Playwright (Headless: True)...")
    with sync_playwright() as p:
        # Launch browser with anti-detection flags
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        # Build context options with realistic user agent
        context_args = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        }

        # Check for saved Facebook session cookies
        if os.path.exists("storage_state.json"):
            print("Loaded logged-in session from storage_state.json")
            context_args["storage_state"] = "storage_state.json"
        else:
            print("Warning: storage_state.json not found. Proceeding without session cookies.")

        context = browser.new_context(**context_args)
        page = context.new_page()

        # Mask navigator.webdriver property to bypass bot checks
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        print("Navigating to Facebook Marketplace...")
        try:
            page.goto(FB_MARKETPLACE_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
        except Exception as e:
            print(f"Navigation warning/timeout: {e}")

        # Dismiss potential cookie or popup overlays
        try:
            close_btn = page.query_selector('div[aria-label="Close"], button:has-text("Allow"), button:has-text("Decline")')
            if close_btn:
                close_btn.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        print("Waiting for page content to populate...")
        try:
            page.wait_for_selector('a[href*="/marketplace/item/"], div[role="main"]', timeout=10000)
        except Exception as e:
            print(f"Selector wait timed out, proceeding to scroll fallback: {e}")

        # Scroll to lazy-load listing cards
        for _ in range(4):
            page.mouse.wheel(0, 1000)
            page.wait_for_timeout(2000)

        # Save debug screenshot for artifact inspection in GitHub Actions
        page.screenshot(path="fb_debug.png", full_page=True)
        print("Debug screenshot saved as fb_debug.png")

        soup = BeautifulSoup(page.content(), "html.parser")
        browser.close()

    # Parse marketplace items
    cards = soup.find_all("a", href=True)
    listing_links = [c for c in cards if "/marketplace/item/" in c["href"]]
    print(f"Found {len(listing_links)} raw listing cards on page.")

    for link in listing_links:
        raw_href = link["href"]
        item_id = raw_href.split("/item/")[1].split("/")[0] if "/item/" in raw_href else raw_href
        full_url = f"https://www.facebook.com{raw_href}" if raw_href.startswith("/") else raw_href

        if item_id not in seen_ids:
            seen_ids.add(item_id)
            # Grab title/text content from card
            text_content = link.get_text(separator=" | ").strip()
            new_matches.append({
                "id": item_id,
                "title": text_content if text_content else "Golf Cart Listing",
                "price": "Check Listing",
                "link": full_url
            })

    print(f"\nScan complete. Total new golf cart matches found: {len(new_matches)}")

    if new_matches:
        save_seen_ids(seen_ids)
        send_email_notification(new_matches)
    else:
        print("No new golf cart listings found on this run.")

if __name__ == "__main__":
    run_scraper()