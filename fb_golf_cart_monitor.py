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
CL_SEARCH_URL = "https://tampa.craigslist.org/search/sss?query=golf%20cart"
NEXTDOOR_SEARCH_URL = "https://nextdoor.com/for_sale_and_free/?query=golf%20cart"

# Email & Session Configuration
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")
NEXTDOOR_SESSION_ID = os.environ.get("NEXTDOOR_SESSION_ID")

def load_seen_ids(filename="seen_golf_cart_ids.json"):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_ids(seen_ids, filename="seen_golf_cart_ids.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, indent=2)

def send_email_notification(new_matches):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAIL:
        print("Email configuration missing. Skipping email dispatch.")
        return

    subject = f"Tampa Golf Cart Alert: {len(new_matches)} New Listing(s) Found!"
    
    body = "<h2>New Golf Cart Listings Found (Tampa Area):</h2><ul>"
    for item in new_matches:
        body += f"<li><strong>[{item['source']}] {item['title']}</strong> - {item['price']}<br><a href='{item['link']}'>View Listing</a></li><br>"
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

        # Load & sanitize Facebook storage_state.json if present
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

        # Inject Nextdoor session cookie cleanly
        if NEXTDOOR_SESSION_ID:
            try:
                context.add_cookies([{
                    "name": "sessionid",
                    "value": str(NEXTDOOR_SESSION_ID).strip(),
                    "url": "https://nextdoor.com"
                }])
                print("Injected NEXTDOOR_SESSION_ID cookie into browser context successfully.")
            except Exception as e:
                print(f"Warning: Could not inject Nextdoor cookie: {e}")

        page = context.new_page()

        # --- 1. SCRAPE CRAIGSLIST (TAMPA) ---
        print("Scraping Tampa Craigslist...")
        try:
            page.goto(CL_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            soup_cl = BeautifulSoup(page.content(), "html.parser")
            cl_results = soup_cl.find_all("li", class_="cl-search-result")
            print(f"Found {len(cl_results)} Craigslist listings.")

            for item in cl_results:
                title_tag = item.find("a", class_="title") or item.find("a", href=True)
                if not title_tag:
                    continue

                link = title_tag["href"]
                title = title_tag.get_text().strip()
                price_tag = item.find("span", class_="price")
                price = price_tag.get_text().strip() if price_tag else "N/A"
                item_id = f"cl_{link.split('/')[-1].replace('.html', '')}"

                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    new_matches.append({
                        "source": "Craigslist",
                        "id": item_id,
                        "title": title,
                        "price": price,
                        "link": link
                    })
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

                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    new_matches.append({
                        "source": "Facebook",
                        "id": item_id,
                        "title": title,
                        "price": price,
                        "link": clean_link
                    })
        except Exception as e:
            print(f"Error scraping Facebook Marketplace: {e}")

        # --- 3. SCRAPE NEXTDOOR ---
        if NEXTDOOR_SESSION_ID:
            print("Scraping Nextdoor For Sale & Free...")
            try:
                page.goto(NEXTDOOR_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)

                # Find listing links on Nextdoor
                cards = page.locator('a[href*="/for_sale_and_free/"]').all()
                print(f"Found {len(cards)} raw Nextdoor elements.")

                for card in cards:
                    href = card.get_attribute("href")
                    if not href or "/item/" not in href:
                        continue
                    
                    clean_link = f"https://nextdoor.com{href.split('?')[0]}"
                    item_id = f"nd_{clean_link.split('/item/')[1].strip('/')}"

                    text = card.inner_text()
                    lines = [line.strip() for line in text.split("\n") if line.strip()]
                    price = lines[0] if lines and "$" in lines[0] else "N/A"
                    title = lines[1] if len(lines) > 1 else (lines[0] if lines else "Nextdoor Golf Cart")

                    if item_id not in seen_ids:
                        seen_ids.add(item_id)
                        new_matches.append({
                            "source": "Nextdoor",
                            "id": item_id,
                            "title": title,
                            "price": price,
                            "link": clean_link
                        })
            except Exception as e:
                print(f"Error scraping Nextdoor: {e}")
        else:
            print("NEXTDOOR_SESSION_ID not present in environment variables. Skipping Nextdoor search.")

        browser.close()

    print(f"Scan complete. Total new matches found across all platforms: {len(new_matches)}")

    if new_matches:
        save_seen_ids(seen_ids)
        send_email_notification(new_matches)
    else:
        print("No new listings found on this run.")

if __name__ == "__main__":
    run_scraper()