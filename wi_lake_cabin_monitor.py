import os
import re
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
STATE_FILE = "seen_cabin_ids.json"
# Removed hardcoded quotes (%22) from the search query
CL_CABIN_URL = "https://northernwi.craigslist.org/search/rea?query=lake+cabin"
REALTOR_CABIN_URL = "https://www.realtor.com/realestateandhomes-search/Vilas-County_WI/type-single-family-home/waterfront"

EXCLUDE_KEYWORDS = [
    "wanted", "looking for", "dock for rent", "timeshare", "boat slip"
]

def load_seen_items():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading state file: {e}")
            return {}
    return {}

def save_seen_items(data):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving state file: {e}")

def extract_clean_id(url, prefix="cl"):
    match = re.search(r'/(\d+)\.html', url)
    if match:
        return f"{prefix}_{match.group(1)}"
    clean_url = url.split("?")[0].rstrip("/")
    return f"{prefix}_{clean_url.split('/')[-1]}"

def clean_price(price_str):
    if not price_str or price_str == "N/A":
        return "N/A"
    digits = re.sub(r'[^\d]', '', price_str)
    if digits:
        return f"${int(digits):,}"
    return "N/A"

def is_valid_cabin(title):
    """Validates if listing matches lake cabin criteria and filters unwanted items."""
    content = title.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in content:
            return False
    return True

def send_email(matches):
    sender = os.getenv("EMAIL_SENDER") or os.getenv("SENDER_EMAIL")
    password = os.getenv("EMAIL_PASSWORD") or os.getenv("SENDER_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER") or os.getenv("RECIPIENT_EMAIL")

    if not sender or not password or not receiver:
        print("Error: Missing email environment variables. Cannot send email.")
        return

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = receiver
    msg['Subject'] = f"WI Lake Cabin Alert: {len(matches)} New Listing(s) / Price Drop(s)"

    body = "<h2>Wisconsin Lake Cabin Monitoring Report</h2><ul>"
    for item in matches:
        body += f"<li><strong>[{item['status']}] {item['source']}</strong>: "
        body += f"<a href='{item['link']}'>{item['title']}</a> - <strong>{item['price']}</strong></li><br>"
    body += "</ul>"

    msg.attach(MIMEText(body, 'html'))

    try:
        print("Connecting to SMTP server...")
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender, password)
            server.send_message(msg)
        print("Email notification sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

def run_scraper():
    seen_items = load_seen_items()
    print(f"Loaded {len(seen_items)} previously seen cabin IDs.")

    new_matches = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900}
        )
        page = context.new_page()

        # --- 1. SCRAPE CRAIGSLIST (NORTHERN WI) ---
        print("Scraping Northern WI Craigslist...")
        cl_added = 0
        try:
            page.goto(CL_CABIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            
            print(f"Craigslist Page Title: {page.title()}")
            cl_items = page.locator('ol.cl-static-search-results > li, li.cl-search-result, div.cl-search-result, a.main').all()
            print(f"Found {len(cl_items)} raw Craigslist result items.")

            for item in cl_items[:40]:
                try:
                    text = item.inner_text().strip()
                    link_elem = item if item.evaluate("el => el.tagName === 'A'") else item.locator("a").first
                    href = link_elem.get_attribute("href")
                    
                    if not href or not text:
                        continue
                    
                    lines = [line.strip() for line in text.split("\n") if line.strip()]
                    title = lines[0] if lines else "Northern WI Property"

                    if not is_valid_cabin(title):
                        continue

                    clean_link = href if href.startswith("http") else f"https://northernwi.craigslist.org{href}"
                    item_id = extract_clean_id(clean_link, prefix="cl")
                    
                    raw_price = next((l for l in lines if "$" in l), "N/A")
                    price = clean_price(raw_price)

                    if item_id not in seen_items:
                        seen_items[item_id] = price
                        new_matches.append({"source": "Craigslist", "id": item_id, "title": title, "price": price, "link": clean_link, "status": "NEW"})
                        cl_added += 1
                        print(f"  + Queued CL: {title} ({price})")
                    elif price != "N/A" and seen_items[item_id] != "N/A" and seen_items[item_id] != price:
                        old_price = seen_items[item_id]
                        seen_items[item_id] = price
                        new_matches.append({"source": "Craigslist", "id": item_id, "title": title, "price": f"{price} (Was {old_price})", "link": clean_link, "status": "PRICE DROP"})
                        cl_added += 1
                        print(f"  + Price Drop CL: {title} ({price})")
                except Exception:
                    continue
            print(f"Craigslist section added {cl_added} items to notification queue.")
        except Exception as e:
            print(f"Error scraping Craigslist: {e}")

        # --- 2. SCRAPE REALTOR.COM (VILAS COUNTY WATERFRONT) ---
        print("Scraping Realtor.com...")
        realtor_added = 0
        try:
            page.goto(REALTOR_CABIN_URL, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(4000)
            
            # Scroll down to trigger lazy loading of property cards
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(2000)

            print(f"Realtor Page Title: {page.title()}")
            cards = page.locator('div[data-testid="property-card"], article[data-testid="property-card"], div.BasePropertyCard').all()
            if not cards:
                cards = page.locator('a[href*="/realestateandhomes-detail/"]').all()

            print(f"Found {len(cards)} raw Realtor.com result items.")

            for card in cards[:25]:
                try:
                    card_text = card.inner_text().strip()
                    href = card.get_attribute("href") if card.evaluate("el => el.tagName === 'A'") else None
                    if not href:
                        link_elem = card.locator('a[href*="/realestateandhomes-detail/"]').first
                        href = link_elem.get_attribute("href") if link_elem.count() > 0 else None
                    
                    if not href:
                        continue

                    clean_link = href if href.startswith("http") else f"https://www.realtor.com{href}"
                    item_id = extract_clean_id(clean_link, prefix="realtor")

                    price_match = re.search(r'\$[\d,]+', card_text)
                    price = price_match.group(0) if price_match else "N/A"

                    text_lines = [l.strip() for l in card_text.split("\n") if len(l.strip()) > 5 and "$" not in l]
                    title = text_lines[0] if text_lines else "Vilas County Lake Property"

                    if item_id not in seen_items:
                        seen_items[item_id] = price
                        new_matches.append({"source": "Realtor.com", "id": item_id, "title": title, "price": price, "link": clean_link, "status": "NEW"})
                        realtor_added += 1
                        print(f"  + Queued Realtor: {title} ({price})")
                    elif price != "N/A" and seen_items[item_id] != "N/A" and seen_items[item_id] != price:
                        old_price = seen_items[item_id]
                        seen_items[item_id] = price
                        new_matches.append({"source": "Realtor.com", "id": item_id, "title": title, "price": f"{price} (Was {old_price})", "link": clean_link, "status": "PRICE DROP"})
                        realtor_added += 1
                        print(f"  + Price Drop Realtor: {title} ({price})")
                except Exception:
                    continue
            print(f"Realtor.com section added {realtor_added} items to notification queue.")
        except Exception as e:
            print(f"Error scraping Realtor.com: {e}")

        browser.close()

    print(f"Scan complete. Total queue length for email dispatch: {len(new_matches)}")

    # Dispatch Email
    if new_matches:
        send_email(matches=new_matches)

    # Save state to track seen IDs
    save_seen_items(seen_items)

if __name__ == "__main__":
    run_scraper()