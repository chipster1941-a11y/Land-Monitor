import os
import sys
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Search URLs for Tampa, FL Region
FB_SEARCH_URL = "https://www.facebook.com/marketplace/tampa/search?query=golf%20cart&exact=false"
CL_SEARCH_URL = "https://tampa.craigslist.org/search/sss?query=golf+cart#search=1~gallery~0~0"
NEXTDOOR_SEARCH_URL = "https://nextdoor.com/search/?query=golf%20cart"

# Email & Session Configuration
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")
NEXTDOOR_SESSION_ID = os.environ.get("NEXTDOOR_SESSION_ID")

# Keywords to ignore (accessory filter)
EXCLUDE_KEYWORDS = [
    "charger", "cover", "enclosure", "tire", "wheel", "rim", 
    "battery", "batteries", "windshield", "seat", "key", "part", "parts"
]

def load_seen_items(filename="seen_golf_cart_ids.json"):
    """Loads a dictionary of {item_id: price} to track new items and price drops."""
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Convert old list format to dict if migrating from old format
                if isinstance(data, list):
                    return {item_id: "N/A" for item_id in data}
                return data
        except Exception:
            return {}
    return {}

def save_seen_items(seen_dict, filename="seen_golf_cart_ids.json"):
    """Saves the updated {item_id: price} dictionary."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(seen_dict, f, indent=2)

def is_valid_cart(title):
    """Returns True if the title looks like a real cart and not an accessory."""
    title_lower = title.lower()
    if "golf" not in title_lower and "cart" not in title_lower:
        return False
    for word in EXCLUDE_KEYWORDS:
        if f" {word}" in title_lower or f"{word}s" in title_lower or title_lower.startswith(word):
            # If it explicitly says "golf cart WITH charger", keep it; otherwise ignore solo accessory listings
            if "with charger" in title_lower or "w/ charger" in title_lower:
                continue
            return False
    return True

def send_email_notification(new_matches):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAIL:
        print("Email configuration missing. Skipping email dispatch.")
        return

    subject = f"Tampa Golf Cart Alert: {len(new_matches)} Update(s) Found!"
    
    body = "<h2>Golf Cart Updates (Tampa Area):</h2><ul>"
    for item in new_matches:
        status_tag = f"<strong style='color:red;'>[{item['status']}]</strong> " if item['status'] != "NEW" else ""
        body += f"<li>{status_tag}<strong>[{item['source']}] {item['title']}</strong> - {item['price']}<br><a href='{item['link']}'>View Listing</a></li><br>"
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
    seen_items = load_seen_items()
    new_matches = []

    print("Starting Playwright Scraper for FB, Craigslist, and Nextdoor...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "viewport": {'width': 1280, 'height': 800}
        }

        # Load Facebook storage_state.json if present
        if os.path.exists("storage_state.json"):
            try:
                with open("storage_state.json", "r", encoding="utf-8") as f:
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
                print("Loaded Facebook storage state successfully.")
            except Exception as e:
                print(f"Warning: Failed to load storage_state.json: {e}")

        context = browser.new_context(**context_args)
        page = context.new_page()

        # --- 1. SCRAPE CRAIGSLIST (TAMPA) ---
        print("Scraping Tampa Craigslist...")
        try:
            page.goto(CL_SEARCH_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            
            cl_items = page.locator('.cl-static-search-result, li.cl-search-result, a.main').all()
            print(f"Found {len(cl_items)} Craigslist result items.")

            for item in cl_items[:20]:
                try:
                    text = item.inner_text().strip()
                    href = item.get_attribute("href") or item.locator("a").get_attribute("href")
                    
                    lines = [line.strip() for line in text.split("\n") if line.strip()]
                    if not href or not lines:
                        continue
                    
                    title = lines[0]
                    if not is_valid_cart(title):
                        continue

                    clean_link = href if href.startswith("http") else f"https://tampa.craigslist.org{href}"
                    item_id = f"cl_{clean_link.split('/')[-1].replace('.html', '')}"
                    price = next((l for l in lines if "$" in l), "N/A")

                    # Check for new listing or price drop
                    if item_id not in seen_items:
                        seen_items[item_id] = price
                        new_matches.append({"source": "Craigslist", "id": item_id, "title": title, "price": price, "link": clean_link, "status": "NEW"})
                    elif seen_items[item_id] != price and price != "N/A":
                        old_price = seen_items[item_id]
                        seen_items[item_id] = price
                        new_matches.append({"source": "Craigslist", "id": item_id, "title": title, "price": f"{price} (Was {old_price})", "link": clean_link, "status": "PRICE DROP"})
                except Exception:
                    continue
        except Exception as e:
            print(f"Error scraping Craigslist: {e}")

        # --- 2. SCRAPE FACEBOOK MARKETPLACE (TAMPA) ---
        print("Scraping Tampa Facebook Marketplace...")
        try:
            page.goto(FB_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            page.evaluate("window.scrollBy(0, 1000);")
            page.wait_for_timeout(2000)

            soup_fb = BeautifulSoup(page.content(), "html.parser")
            fb_cards = soup_fb.find_all("a", href=lambda href: href and "/marketplace/item/" in href)
            print(f"Found {len(fb_cards)} Facebook Marketplace cards.")

            for card in fb_cards:
                raw_href = card["href"]
                clean_link = f"https://www.facebook.com{raw_href.split('?')[0]}"
                item_id = f"fb_{clean_link.split('/item/')[1].strip('/')}"

                card_text = [t.strip() for t in card.stripped_strings if t.strip()]
                price = card_text[0] if card_text else "N/A"
                title = card_text[1] if len(card_text) > 1 else "Golf Cart Listing"

                if not is_valid_cart(title):
                    continue

                if item_id not in seen_items:
                    seen_items[item_id] = price
                    new_matches.append({"source": "Facebook", "id": item_id, "title": title, "price": price, "link": clean_link, "status": "NEW"})
                elif seen_items[item_id] != price and price != "N/A":
                    old_price = seen_items[item_id]
                    seen_items[item_id] = price
                    new_matches.append({"source": "Facebook", "id": item_id, "title": title, "price": f"{price} (Was {old_price})", "link": clean_link, "status": "PRICE DROP"})
        except Exception as e:
            print(f"Error scraping Facebook Marketplace: {e}")

        # --- 3. SCRAPE NEXTDOOR ---
        if NEXTDOOR_SESSION_ID:
            print("Scraping Nextdoor For Sale & Free...")
            try:
                nd_page = context.new_page()
                nd_page.set_extra_http_headers({"Cookie": f"sessionid={NEXTDOOR_SESSION_ID.strip()}"})
                
                nd_page.goto(NEXTDOOR_SEARCH_URL, wait_until="networkidle", timeout=30000)
                nd_page.wait_for_timeout(4000)

                nd_cards = nd_page.locator('a[href*="/for_sale_and_free/"], a[href*="/post/"]').all()
                print(f"Found {len(nd_cards)} raw Nextdoor elements.")

                for card in nd_cards:
                    try:
                        href = card.get_attribute("href")
                        text = card.inner_text().strip()
                        lines = [line.strip() for line in text.split("\n") if line.strip()]
                        
                        if not href or not lines:
                            continue
                        
                        price = next((l for l in lines if "$" in l), "N/A")
                        title = lines[0] if lines[0] != price else (lines[1] if len(lines) > 1 else "Nextdoor Item")

                        if not is_valid_cart(title):
                            continue

                        clean_link = href if href.startswith("http") else f"https://nextdoor.com{href.split('?')[0]}"
                        item_id = f"nd_{clean_link.split('/')[-1].strip('/')}"

                        if item_id not in seen_items:
                            seen_items[item_id] = price
                            new_matches.append({"source": "Nextdoor", "id": item_id, "title": title, "price": price, "link": clean_link, "status": "NEW"})
                        elif seen_items[item_id] != price and price != "N/A":
                            old_price = seen_items[item_id]
                            seen_items[item_id] = price
                            new_matches.append({"source": "Nextdoor", "id": item_id, "title": title, "price": f"{price} (Was {old_price})", "link": clean_link, "status": "PRICE DROP"})
                    except Exception:
                        continue
                nd_page.close()
            except Exception as e:
                print(f"Error scraping Nextdoor: {e}")

        browser.close()

    print(f"Scan complete. Found {len(new_matches)} new or updated listing(s).")

    if new_matches:
        save_seen_items(seen_items)
        send_email_notification(new_matches)
    else:
        print("No new listings or price drops on this run.")

if __name__ == "__main__":
    run_scraper()