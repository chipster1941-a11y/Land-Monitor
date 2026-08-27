import os
import sys
import json
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- CONFIGURATION & SEARCH URLS ---
# Wisconsin Lake Cabin Search Queries
CL_CABIN_URL = "https://northernwi.craigslist.org/search/rea?query=lake+cabin#search=1~gallery~0~0"
REALTOR_CABIN_URL = "https://www.realtor.com/realestateandhomes-search/Wisconsin/type-single-family-home/keyword-lakefront"
ZILLOW_CABIN_URL = "https://www.zillow.com/wi/houses/lakefront_att/"

# Email Credentials
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")

# Keywords for Filtering
INCLUDE_KEYWORDS = ["lake", "lakefront", "cabin", "waterfront", "flowage", "pond", "river"]
EXCLUDE_KEYWORDS = ["mobile home", "lot only", "vacant land", "rv", "camper", "boat slip"]

# --- HELPER FUNCTIONS ---

def clean_price(price_str):
    """Standardizes price text into pure numeric string (e.g., '$250,000' -> '$250000') to avoid false price drops."""
    if not price_str or price_str == "N/A":
        return "N/A"
    nums = re.sub(r'[^\d]', '', str(price_str))
    return f"${nums}" if nums else "N/A"

def extract_clean_id(url, prefix="item"):
    """Extracts a unique, clean ID from any listing URL, stripping query parameters and tracking hashes."""
    if not url:
        return None
    clean_url = url.split("?")[0].split("#")[0].rstrip("/")
    last_segment = clean_url.split("/")[-1].replace(".html", "")
    return f"{prefix}_{last_segment}"

def load_seen_items(filename="seen_cabin_ids.json"):
    """Loads previously seen listings from state tracking file."""
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {item_id: "N/A" for item_id in data}
                return data
        except Exception as e:
            print(f"Warning: Could not read {filename}: {e}")
            return {}
    return {}

def save_seen_items(seen_dict, filename="seen_cabin_ids.json"):
    """Saves updated seen listing dictionary to disk."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(seen_dict, f, indent=2)

def is_valid_cabin(title, description=""):
    """Validates if listing matches lake cabin criteria and filters unwanted items."""
    content = f"{title} {description}".lower()
    
    # Check for excluded words
    for kw in EXCLUDE_KEYWORDS:
        if kw in content:
            return False

    # Ensure at least one target keyword is present
    return any(kw in content for kw in INCLUDE_KEYWORDS)

def send_email_notification(new_matches):
    """Formats and sends an HTML email notification sorted by source platform."""
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAIL:
        print("Email configuration missing. Skipping email dispatch.")
        return

    subject = f"WI Lake Cabin Alert: {len(new_matches)} Update(s) Found!"
    sorted_matches = sorted(new_matches, key=lambda x: x['source'])
    
    body = f"<h2>Wisconsin Lake Cabin Updates ({len(sorted_matches)} total):</h2><ul>"
    for item in sorted_matches:
        status_tag = f"<strong style='color:red;'>[{item.get('status', 'NEW')}]</strong> " if item.get('status') != "NEW" else ""
        source_tag = f"<strong>[{item.get('source', 'Unknown')}]</strong>"
        title_text = item.get('title', 'No Title')
        price_text = item.get('price', 'N/A')
        link_url = item.get('link', '#')
        
        body += f"<li>{status_tag}{source_tag} {title_text} - {price_text}<br><a href='{link_url}'>View Listing</a></li><br>"
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
        print(f"Email alert sent successfully with {len(sorted_matches)} listings!")
    except Exception as e:
        print(f"Failed to send email alert: {e}")

# --- MAIN SCRAPER ROUTINE ---

def run_scraper():
    seen_items = load_seen_items()
    print(f"Loaded {len(seen_items)} previously seen cabin IDs.")
    new_matches = []

    print("Starting Playwright Scraper for WI Lake Cabins...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        page = context.new_page()

        # --- 1. SCRAPE CRAIGSLIST (NORTHERN WI) ---
        print("Scraping Northern WI Craigslist...")
        cl_added = 0
        try:
            page.goto(CL_CABIN_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            
            cl_items = page.locator('.cl-static-search-result, li.cl-search-result, a.main').all()
            print(f"Found {len(cl_items)} raw Craigslist result items.")

            for item in cl_items[:40]:
                try:
                    text = item.inner_text().strip()
                    href = item.get_attribute("href") or item.locator("a").get_attribute("href")
                    
                    lines = [line.strip() for line in text.split("\n") if line.strip()]
                    if not href or not lines:
                        continue
                    
                    title = lines[0]
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
                    elif price != "N/A" and seen_items[item_id] != "N/A" and seen_items[item_id] != price:
                        old_price = seen_items[item_id]
                        seen_items[item_id] = price
                        new_matches.append({"source": "Craigslist", "id": item_id, "title": title, "price": f"{price} (Was {old_price})", "link": clean_link, "status": "PRICE DROP"})
                        cl_added += 1
                except Exception:
                    continue
            print(f"Craigslist section added {cl_added} items to notification queue.")
        except Exception as e:
            print(f"Error scraping Craigslist: {e}")

        # --- 2. SCRAPE REALTOR.COM (WISCONSIN LAKEFRONT) ---
        print("Scraping Realtor.com...")
        realtor_added = 0
        try:
            page.goto(REALTOR_CABIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            soup_realtor = BeautifulSoup(page.content(), "html.parser")
            cards = soup_realtor.find_all("div", class_=lambda c: c and "BasePropertyCard" in c) or soup_realtor.find_all("li", class_=lambda c: c and "resrc" in c)
            print(f"Found {len(cards)} Realtor.com listing cards.")

            for card in cards:
                try:
                    link_elem = card.find("a", href=True)
                    if not link_elem:
                        continue

                    raw_href = link_elem["href"]
                    clean_link = raw_href if raw_href.startswith("http") else f"https://www.realtor.com{raw_href}"
                    item_id = extract_clean_id(clean_link, prefix="realtor")

                    text_content = [t.strip() for t in card.stripped_strings if t.strip()]
                    if not text_content:
                        continue

                    raw_price = next((t for t in text_content if "$" in t), "N/A")
                    price = clean_price(raw_price)
                    title = next((t for t in text_content if len(t) > 10 and "$" not in t), "Wisconsin Lakefront Home")

                    if item_id not in seen_items:
                        seen_items[item_id] = price
                        new_matches.append({"source": "Realtor.com", "id": item_id, "title": title, "price": price, "link": clean_link, "status": "NEW"})
                        realtor_added += 1
                    elif price != "N/A" and seen_items[item_id] != "N/A" and seen_items[item_id] != price:
                        old_price = seen_items[item_id]
                        seen_items[item_id] = price
                        new_matches.append({"source": "Realtor.com", "id": item_id, "title": title, "price": f"{price} (Was {old_price})", "link": clean_link, "status": "PRICE DROP"})
                        realtor_added += 1
                except Exception:
                    continue
            print(f"Realtor.com section added {realtor_added} items to notification queue.")
        except Exception as e:
            print(f"Error scraping Realtor.com: {e}")

        # --- 3. SCRAPE ZILLOW (WISCONSIN LAKEFRONT) ---
        print("Scraping Zillow...")
        zillow_added = 0
        try:
            page.goto(ZILLOW_CABIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            soup_zillow = BeautifulSoup(page.content(), "html.parser")
            z_cards = soup_zillow.find_all("article", class_=lambda c: c and "property-card" in c)
            print(f"Found {len(z_cards)} Zillow cards.")

            for card in z_cards:
                try:
                    link_elem = card.find("a", href=True)
                    if not link_elem:
                        continue
                    
                    raw_href = link_elem["href"]
                    clean_link = raw_href if raw_href.startswith("http") else f"https://www.zillow.com{raw_href}"
                    item_id = extract_clean_id(clean_link, prefix="zillow")

                    text_content = [t.strip() for t in card.stripped_strings if t.strip()]
                    if not text_content:
                        continue

                    raw_price = next((t for t in text_content if "$" in t), "N/A")
                    price = clean_price(raw_price)
                    title = next((t for t in text_content if "Bds" in t or "Bed" in t or "St" in t or "Rd" in t), "Zillow Lakefront Property")

                    if item_id not in seen_items:
                        seen_items[item_id] = price
                        new_matches.append({"source": "Zillow", "id": item_id, "title": title, "price": price, "link": clean_link, "status": "NEW"})
                        zillow_added += 1
                    elif price != "N/A" and seen_items[item_id] != "N/A" and seen_items[item_id] != price:
                        old_price = seen_items[item_id]
                        seen_items[item_id] = price
                        new_matches.append({"source": "Zillow", "id": item_id, "title": title, "price": f"{price} (Was {old_price})", "link": clean_link, "status": "PRICE DROP"})
                        zillow_added += 1
                except Exception:
                    continue
            print(f"Zillow section added {zillow_added} items to notification queue.")
        except Exception as e:
            print(f"Error scraping Zillow: {e}")

        browser.close()

    print(f"Scan complete. Total queue length for email dispatch: {len(new_matches)}")

    if new_matches:
        save_seen_items(seen_items)
        send_email_notification(new_matches)
    else:
        print("No new listings or price drops on this run.")

if __name__ == "__main__":
    run_scraper()