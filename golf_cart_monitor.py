import os
import re
import json
import csv
import smtplib
import urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ==========================================
# CONFIGURATION
# ==========================================
MAX_GOLF_CART_PRICE = 10000  # Price cap for golf carts

KEY_WORDS = [
    "yamaha", "drive2", "quietech", "efi", 
    "ezgo", "e-z-go", "express s4", "club car", 
    "golf cart", "golf car", "cart", "buggy"
]

# Corrected Craigslist Search Enpoints
CRAIGSLIST_URLS = [
    {"location": "Tampa", "url": "https://tampa.craigslist.org/search/?query=golf+cart"},
    {"location": "Sarasota", "url": "https://sarasota.craigslist.org/search/?query=golf+cart"}
]

FB_URLS = [
    {"location": "Tampa", "url": "https://www.facebook.com/marketplace/tampa/search?query=golf%20cart"},
    {"location": "Sarasota", "url": "https://www.facebook.com/marketplace/sarasota/search?query=golf%20cart"}
]

NEXTDOOR_URLS = [
    {"location": "Tampa/Sarasota Area", "url": "https://nextdoor.com/for_sale_and_free/?query=golf%20cart"}
]

SEEN_FILE = "seen_carts.json"
CSV_FILE = "matched_carts.csv"
STORAGE_STATE_FILE = "fb_state.json"

EMAIL_SENDER = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")


# ==========================================
# HELPERS
# ==========================================
def parse_price(price_str):
    if not price_str or any(w in str(price_str).upper() for w in ["N/A", "FREE", "CONTACT"]):
        return None
    match = re.search(r'\$?\s*([\d,]+(?:\.\d+)?)', str(price_str))
    if not match:
        return None
    try:
        return float(match.group(1).replace(',', ''))
    except ValueError:
        return None


def load_seen_items():
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_seen_items(seen_data):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen_data, f, indent=2)


def extract_fb_id(url):
    match = re.search(r'/item/(\d+)', url)
    if match:
        return f"fb_{match.group(1)}"
    return None


def is_valid_cart(title):
    if not title:
        return False
    title_lower = title.lower()
    return any(k in title_lower for k in KEY_WORDS)


def process_item_match(item_id, title, price_str, link, source, seen_items, new_matches):
    num_price = parse_price(price_str)
    
    # Filter out items above budget cap
    if num_price is not None and num_price > MAX_GOLF_CART_PRICE:
        return False

    # Price drop or existing listing check
    if item_id in seen_items:
        old_price_str = seen_items[item_id].get("price", "")
        old_num_price = parse_price(old_price_str)
        
        if num_price and old_num_price and num_price < old_num_price:
            print(f"  [PRICE DROP] {title} reduced from {old_price_str} to {price_str}")
            item_data = {
                "id": item_id,
                "title": f"[PRICE DROP] {title}",
                "price": price_str,
                "old_price": old_price_str,
                "location": source,
                "link": link,
                "source": source
            }
            new_matches.append(item_data)
            seen_items[item_id]["price"] = price_str
            save_seen_items(seen_items)
            return True
        return False

    # New listing match
    item_data = {
        "id": item_id,
        "title": title,
        "price": price_str,
        "location": source,
        "link": link,
        "source": source
    }
    new_matches.append(item_data)
    seen_items[item_id] = {"title": title, "price": price_str, "date": datetime.now().isoformat()}
    save_seen_items(seen_items)
    return True


# ==========================================
# MAIN SCRAPER
# ==========================================
def run_golf_cart_scraper():
    print("Starting Playwright Scraper for FB, Craigslist, and Nextdoor...")
    seen_items = load_seen_items()
    new_matches = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context_kwargs = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        if os.path.exists(STORAGE_STATE_FILE):
            context_kwargs["storage_state"] = STORAGE_STATE_FILE
            print("Loaded Facebook storage state successfully.")

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # ------------------------------------------
        # 1. CRAIGSLIST SCRAPER
        # ------------------------------------------
        print("Scraping Craigslist (Tampa & Sarasota)...")
        cl_added = 0
        for entry in CRAIGSLIST_URLS:
            loc = entry["location"]
            print(f"Checking {loc} Craigslist...")
            try:
                page.goto(entry["url"], wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)  # Allow JS execution
                
                # Broad target selector matching cards
                items = (
                    page.query_selector_all('li.cl-search-result') or 
                    page.query_selector_all('ol.cl-static-search-results li') or 
                    page.query_selector_all('li.result-row') or
                    page.query_selector_all('a.cl-app-anchor')
                )
                print(f"Found {len(items)} raw {loc} Craigslist result items.")

                for item in items:
                    a_elem = item.query_selector('a.title, a.posting-title, a.main-link, a') if item.name != 'a' else item
                    if not a_elem:
                        continue
                    
                    link = a_elem.get_attribute("href") or ""
                    title = a_elem.inner_text().strip()

                    post_id = item.get_attribute("data-pid")
                    if not post_id:
                        id_match = re.search(r'/(\d+)\.html', link)
                        post_id = id_match.group(1) if id_match else None

                    if not post_id:
                        continue

                    full_id = f"cl_cart_{post_id}"

                    price_elem = item.query_selector('.price, .property-price, span.price')
                    price_str = price_elem.inner_text().strip() if price_elem else "N/A"

                    if not is_valid_cart(title):
                        continue

                    if process_item_match(full_id, title, price_str, link, f"Craigslist ({loc})", seen_items, new_matches):
                        cl_added += 1

            except Exception as e:
                print(f"Error scraping {loc} Craigslist: {e}")

        print(f"Craigslist section added {cl_added} total items to notification queue.")

        # ------------------------------------------
        # 2. FACEBOOK MARKETPLACE SCRAPER
        # ------------------------------------------
        print("Scraping Facebook Marketplace (Tampa & Sarasota)...")
        fb_added = 0
        for entry in FB_URLS:
            loc = entry["location"]
            print(f"Checking {loc} Facebook Marketplace...")
            try:
                page.goto(entry["url"], wait_until="domcontentloaded", timeout=25000)
                
                # Scroll down to trigger GraphQL hydration
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(4000)

                # Query card links directly in DOM
                card_elements = page.query_selector_all('a[href*="/marketplace/item/"]')
                print(f"Found {len(card_elements)} Facebook Marketplace cards for {loc}.")

                for elem in card_elements:
                    raw_href = elem.get_attribute("href") or ""
                    item_id = extract_fb_id(raw_href)
                    if not item_id:
                        continue

                    clean_link = f"https://www.facebook.com/marketplace/item/{item_id.replace('fb_', '')}/"

                    raw_text = elem.inner_text().strip()
                    card_text = [t.strip() for t in raw_text.split("\n") if t.strip()]
                    if not card_text:
                        continue

                    price = card_text[0] if "$" in card_text[0] else ("N/A" if not any("$" in t for t in card_text) else next(t for t in card_text if "$" in t))
                    title = card_text[1] if len(card_text) > 1 and card_text[1] != price else card_text[0]

                    if not is_valid_cart(title):
                        continue

                    if process_item_match(item_id, title, price, clean_link, f"Facebook ({loc})", seen_items, new_matches):
                        fb_added += 1

            except Exception as e:
                print(f"Error scraping {loc} Facebook Marketplace: {e}")

        print(f"Facebook section added {fb_added} total items to notification queue.")

        # ------------------------------------------
        # 3. NEXTDOOR SCRAPER
        # ------------------------------------------
        print("Scraping Nextdoor For Sale & Free...")
        nd_added = 0
        for entry in NEXTDOOR_URLS:
            loc = entry["location"]
            try:
                page.goto(entry["url"], wait_until="domcontentloaded", timeout=20000)
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(3000)
                
                nd_cards = page.query_selector_all('div[data-testid="item-card"], a[href*="/for_sale_and_free/"]')
                print(f"Found {len(nd_cards)} raw Nextdoor elements.")

                for card in nd_cards:
                    raw_text = card.inner_text().strip()
                    card_text = [t.strip() for t in raw_text.split("\n") if t.strip()]
                    if not card_text:
                        continue

                    a_elem = card.query_selector('a[href]') if card.name != "a" else card
                    link = a_elem.get_attribute("href") if a_elem else ""
                    if not link:
                        continue

                    full_link = f"https://nextdoor.com{link}" if link.startswith("/") else link
                    
                    id_match = re.search(r'/(\d+)', link)
                    nd_id = f"nd_{id_match.group(1)}" if id_match else f"nd_{hash(full_link)}"

                    price = next((t for t in card_text if "$" in t), "N/A")
                    title = card_text[0] if card_text[0] != price else (card_text[1] if len(card_text) > 1 else "Nextdoor Golf Cart")

                    if not is_valid_cart(title):
                        continue

                    if process_item_match(nd_id, title, price, full_link, f"Nextdoor ({loc})", seen_items, new_matches):
                        nd_added += 1

            except Exception as e:
                print(f"Error scraping Nextdoor: {e}")

        print(f"Nextdoor section added {nd_added} items to notification queue.")
        browser.close()

    print(f"\nScan complete. Total queue length for email dispatch: {len(new_matches)}")
    print(f"DEBUG - Currently stored seen items count: {len(seen_items)}")
    print(f"DEBUG - Matches queued for email: {new_matches}")

    if new_matches:
        send_email_alert(new_matches)
    else:
        print("No new listings or price drops on this run.")


# ==========================================
# EMAIL NOTIFICATIONS
# ==========================================
def send_email_alert(matches):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email credentials missing. Skipping email send.")
        return

    count = len(matches)
    first_match = matches[0]

    subject = f"🛺 Golf Cart Alert: {first_match['price']} | {first_match['title']}"
    if count > 1:
        subject = f"🛺 {count} New Golf Cart Matches Found!"

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f1f5f9; padding: 20px 10px; color: #333;">
        <div style="max-width: 600px; margin: auto; background-color: #ffffff; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.08);">
            <h2 style="background-color: #2563eb; color: white; padding: 14px; border-radius: 6px; text-align: center; margin-top: 0;">
                🛺 Golf Cart Listing Alert ({count})
            </h2>
            <p style="color: #475569; font-size: 14px;">New golf cart listings or price drops in Tampa / Sarasota:</p>
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin-bottom: 20px;">
    """

    for item in matches:
        html_content += f"""
        <div style="border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background-color: #f8fafc;">
            <span style="background-color: #2563eb; color: white; padding: 2px 8px; font-size: 11px; border-radius: 4px; font-weight: bold; text-transform: uppercase;">
                {item['source']}
            </span>
            <h3 style="margin: 8px 0 4px 0; color: #0f172a; font-size: 16px;">{item['title']}</h3>
            <p style="margin: 2px 0; font-size: 14px;"><strong>Price:</strong> <span style="color: #16a34a; font-weight: bold;">{item['price']}</span></p>
            <div style="margin-top: 12px;">
                <a href="{item['link']}" target="_blank" style="background-color: #2563eb; color: white; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 13px; font-weight: bold; display: inline-block;">
                    View Listing &rarr;
                </a>
            </div>
        </div>
        """

    html_content += """
            <p style="font-size: 12px; color: #94a3b8; text-align: center; margin-top: 24px;">
                Tampa/Sarasota Golf Cart Monitor • Automated GitHub Action
            </p>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"Email alert sent successfully for {count} golf cart match(es)!")
    except Exception as e:
        print(f"Failed to send email alert: {e}")


if __name__ == "__main__":
    run_golf_cart_scraper()