import os
import re
import csv
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
FB_MARKETPLACE_URL = "https://www.facebook.com/marketplace/tampa/search?query=golf%20cart"
SEEN_FILE = "seen_golf_carts.txt"
CSV_FILE = "matched_golf_carts.csv"

# Keyword and Price Filtering
KEYWORDS = ["golf cart", "golf cart", "ezgo", "club car", "yamaha", "icon", "evolution"]
EXCLUDE_KEYWORDS = ["wanted", "looking for", "parts only", "tire", "battery", "charger", "cover", "windshield"]
MAX_PRICE = 5000  # Adjust maximum budget here

# Email Configuration from Environment Variables
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

def load_seen_ids():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_seen_id(item_id):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{item_id}\n")

def save_to_csv(item_id, title, price, url):
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["ID", "Title", "Price", "URL"])
        writer.writerow([item_id, title, price, url])

def send_email_alert(new_matches):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAIL:
        print("Email credentials not fully set. Skipping email alert.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚨 {len(new_matches)} New Golf Cart Listing(s) Found!"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    html_content = "<h2>New Golf Cart Matches Found:</h2><ul>"
    for item in new_matches:
        html_content += f"""
        <li>
            <strong><a href="{item['url']}">{item['title']}</a></strong> - ${item['price']}<br>
            <a href="{item['url']}">View Listing on Facebook Marketplace</a>
        </li><br>
        """
    html_content += "</ul>"

    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print(f"Successfully sent email notification for {len(new_matches)} items.")
    except Exception as e:
        print(f"Error sending email: {e}")

def scrape_facebook():
    seen_ids = load_seen_ids()
    new_matches = []

    headless = os.environ.get("CI") == "true" or os.environ.get("HEADLESS") == "true"
    print(f"Launching Playwright (Headless: {headless})...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )

        # Load session state from portable JSON file if present
        context_args = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        }
        if os.path.exists("storage_state.json"):
            print("Loaded logged-in session from storage_state.json")
            context_args["storage_state"] = "storage_state.json"
        else:
            print("Warning: storage_state.json not found. Proceeding without session cookies.")

        context = browser.new_context(**context_args)
        page = context.new_page()

        # Mask navigator.webdriver property
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        print("Navigating to Facebook Marketplace...")
        page.goto(FB_MARKETPLACE_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        # Dismiss potential cookie or location overlays
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

        # Scroll to trigger lazy loading of cards & thumbnails
        for _ in range(4):
            page.mouse.wheel(0, 1000)
            page.wait_for_timeout(2000)

        # Save debug screenshot for artifact inspection
        page.screenshot(path="fb_debug.png", full_page=True)
        print("Debug screenshot saved as fb_debug.png")

        soup = BeautifulSoup(page.content(), "html.parser")
        browser.close()

    # Parse listing links
    cards = soup.find_all("a", href=re.compile(r"/marketplace/item/\d+"))
    print(f"Found {len(cards)} raw listing cards on page.")

    for card in cards:
        href = card.get("href", "")
        match = re.search(r"/marketplace/item/(\d+)", href)
        if not match:
            continue

        item_id = match.group(1)
        if item_id in seen_ids:
            continue

        full_url = f"https://www.facebook.com/marketplace/item/{item_id}/"
        text_content = card.get_text(separator=" ").strip()

        # Price parsing
        price_match = re.search(r"\$([0-9,]+)", text_content)
        price = int(price_match.group(1).replace(",", "")) if price_match else 0

        # Title parsing
        title = text_content.replace(f"${price}", "").strip() if price else text_content
        title_lower = title.lower()

        # Keyword matching and filtering
        if any(kw in title_lower for kw in KEYWORDS) and not any(ex in title_lower for ex in EXCLUDE_KEYWORDS):
            if price == 0 or price <= MAX_PRICE:
                print(f"✨ Match Found: {title} | ${price} | {full_url}")
                new_matches.append({"id": item_id, "title": title, "price": price, "url": full_url})
                save_seen_id(item_id)
                seen_ids.add(item_id)
                save_to_csv(item_id, title, price, full_url)

    print(f"\nScan complete. Total new golf cart matches found: {len(new_matches)}")
    if new_matches:
        send_email_alert(new_matches)
    else:
        print("No new golf cart listings found on this run.")

if __name__ == "__main__":
    scrape_facebook()