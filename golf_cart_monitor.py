import os
import json
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ----------------- CONFIGURATION -----------------
SEARCH_QUERY = "golf cart"

# Tampa/Sarasota FL local targeting
LOCAL_ZIP = "34236"  # Sarasota, FL area zip code
LOCAL_LATITUDE = 27.3364
LOCAL_LONGITUDE = -82.5307

# Search URLs
NEXTDOOR_SEARCH_URL = f"https://nextdoor.com/for_sale_and_free/?query={SEARCH_QUERY.replace(' ', '%20')}"
OFFERUP_SEARCH_URL = f"https://offerup.com/search?q={SEARCH_QUERY.replace(' ', '%20')}&delivery_param=p&zip={LOCAL_ZIP}&radius=30"

SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")
NEXTDOOR_SESSION_ID = os.environ.get("NEXTDOOR_SESSION_ID")

SEEN_IDS_FILE = "seen_ids.json"

def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        try:
            with open(SEEN_IDS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_ids(seen_ids):
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(seen_ids), f)

# ----------------- NEXTDOOR SCRAPER -----------------
def scrape_nextdoor(seen_ids):
    print(f"🛒 Checking Nextdoor for '{SEARCH_QUERY}' listings...")
    matches = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )

            if NEXTDOOR_SESSION_ID:
                cookies = []
                raw_cookie_str = NEXTDOOR_SESSION_ID.strip()

                if "=" in raw_cookie_str:
                    for item in raw_cookie_str.split(";"):
                        item = item.strip()
                        if not item or "=" not in item:
                            continue
                        
                        name, val = item.split("=", 1)
                        name, val = name.strip(), val.strip()

                        if not name or name.lower() in ["path", "domain", "expires", "secure", "httponly", "samesite"]:
                            continue

                        cookies.append({
                            "name": name,
                            "value": val,
                            "domain": ".nextdoor.com",
                            "path": "/"
                        })
                else:
                    cookies.append({
                        "name": "ndp_session",
                        "value": raw_cookie_str,
                        "domain": ".nextdoor.com",
                        "path": "/"
                    })

                if cookies:
                    context.add_cookies(cookies)

            page = context.new_page()

            print(f" -> Navigating to Nextdoor search: {NEXTDOOR_SEARCH_URL}")
            page.goto(NEXTDOOR_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            soup = BeautifulSoup(page.content(), "html.parser")

            cards = (
                soup.select('a[href*="/p/"]')
                or soup.select('a[href*="/for_sale_and_free/"]')
                or soup.select('div[data-testid]')
            )

            print(f" -> Found {len(cards)} raw candidate cards on Nextdoor.")

            for card in cards:
                href = card.get("href", "") if card.name == "a" else (card.find("a", href=True) or {}).get("href", "")
                
                if not href or href.strip("/") in ["for_sale_and_free", "for_sale_and_free/"] or "query=" in href:
                    continue

                item_id = href.strip("/").split("/")[-1]
                if item_id in ["for_sale_and_free", "finds"]:
                    continue

                full_id = f"nd_cart_{item_id}"

                if full_id in seen_ids:
                    continue

                full_url = f"https://nextdoor.com{href}" if href.startswith("/") else href

                text_content = card.get_text(separator=" ").strip()
                if not text_content or len(text_content) < 5:
                    continue

                lines = [line.strip() for line in text_content.split("\n") if line.strip()]
                
                title = lines[0] if lines else f"Golf Cart Listing ({item_id})"
                if title.lower() in ["for sale & free", "for sale and free", "search", "nextdoor"]:
                    continue

                price = "Check Listing"
                for line in lines:
                    if "$" in line:
                        price = line
                        break

                matches.append({
                    "id": full_id,
                    "title": title[:100],
                    "price": price,
                    "location": "Nextdoor Local Area",
                    "link": full_url,
                    "source": "Nextdoor"
                })

            browser.close()

    except Exception as e:
        print(f"Error checking Nextdoor: {e}")

    return matches

# ----------------- OFFERUP SCRAPER -----------------
def scrape_offerup(seen_ids):
    print(f"🏷️  Checking OfferUp for '{SEARCH_QUERY}' listings...")
    matches = []

    try:
        with sync_playwright() as p:
            # Stealth browser launch
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
                geolocation={"latitude": LOCAL_LATITUDE, "longitude": LOCAL_LONGITUDE},
                permissions=["geolocation"],
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
                }
            )

            # Pre-inject location cookies for OfferUp
            context.add_cookies([
                {"name": "ou_zipcode", "value": LOCAL_ZIP, "domain": ".offerup.com", "path": "/"},
                {"name": "ou_latitude", "value": str(LOCAL_LATITUDE), "domain": ".offerup.com", "path": "/"},
                {"name": "ou_longitude", "value": str(LOCAL_LONGITUDE), "domain": ".offerup.com", "path": "/"}
            ])
            
            page = context.new_page()
            # Mask navigator.webdriver automation flag
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            print(f" -> Navigating to OfferUp search: {OFFERUP_SEARCH_URL}")
            page.goto(OFFERUP_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            
            page.wait_for_timeout(4000)
            page.evaluate("window.scrollBy(0, 1000)")
            page.wait_for_timeout(3000)

            page_title = page.title()
            html_content = page.content()
            soup = BeautifulSoup(html_content, "html.parser")

            # Strategy 1: Check Next.js script payload directly
            next_data_script = soup.find("script", id="__NEXT_DATA__")
            if next_data_script and next_data_script.string:
                try:
                    payload = json.loads(next_data_script.string)
                    # Recursively search for feed items in JSON
                    payload_str = json.dumps(payload)
                    item_ids = set(re.findall(r'"/item/detail/(\d+)"', payload_str) + re.findall(r'"id":"(\d+)"', payload_str))
                    
                    for i_id in item_ids:
                        full_id = f"ou_cart_{i_id}"
                        if full_id not in seen_ids:
                            matches.append({
                                "id": full_id,
                                "title": "Golf Cart Listing (OfferUp)",
                                "price": "Check Listing",
                                "location": "Tampa/Sarasota Area (OfferUp)",
                                "link": f"https://offerup.com/item/detail/{i_id}",
                                "source": "OfferUp"
                            })
                except Exception as e:
                    print(f" -> JSON payload parse notice: {e}")

            # Strategy 2: HTML Cards fallback
            if not matches:
                cards = (
                    soup.select('a[href*="/item/"]') 
                    or soup.select('a[href*="/item/detail/"]') 
                    or soup.select('div[data-testid*="item"]')
                    or soup.select('a[aria-label*="$"]')
                    or soup.select('a[data-testid*="listing"]')
                )
                print(f" -> Found {len(cards)} raw candidate cards on OfferUp.")

                if len(cards) == 0:
                    print(f" ℹ️ [OfferUp Diagnostic] Page title returned: '{page_title}'")

                for card in cards:
                    href = card.get("href", "") if card.name == "a" else (card.find("a", href=True) or {}).get("href", "")
                    if not href or "/item/" not in href:
                        continue

                    item_id = href.strip("/").split("/")[-1]
                    full_id = f"ou_cart_{item_id}"

                    if full_id in seen_ids:
                        continue

                    full_url = f"https://offerup.com{href}" if href.startswith("/") else href
                    text_content = card.get_text(separator=" ").strip()
                    
                    lines = [line.strip() for line in text_content.split("\n") if line.strip()]
                    if not lines:
                        continue

                    price = "Check Listing"
                    title = "Golf Cart Listing"

                    for line in lines:
                        if "$" in line and price == "Check Listing":
                            price = line
                        elif len(line) > 3 and title == "Golf Cart Listing":
                            title = line

                    full_text_upper = text_content.upper()
                    found_states = re.findall(r',\s*([A-Z]{2})\b', full_text_upper)
                    
                    if found_states and any(state != "FL" for state in found_states):
                        continue

                    if any(state_name in full_text_upper for state_name in ["KANSAS", "CALIFORNIA", "TEXAS", "NEW YORK"]):
                        continue

                    matches.append({
                        "id": full_id,
                        "title": title[:100],
                        "price": price,
                        "location": "Tampa/Sarasota Area (OfferUp)",
                        "link": full_url,
                        "source": "OfferUp"
                    })
            else:
                print(f" -> Found {len(matches)} raw candidate listings via OfferUp JSON payload.")

            browser.close()

    except Exception as e:
        print(f"Error checking OfferUp: {e}")

    return matches

# ----------------- EMAIL NOTIFICATIONS -----------------
def send_email_alert(new_matches):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAIL:
        print("Skipping email dispatch: Missing email credentials.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚨 {len(new_matches)} New Golf Cart Listing(s) Found!"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    html_items = ""
    for item in new_matches:
        html_items += f"""
        <div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 15px; margin-bottom: 15px; font-family: Arial, sans-serif;">
            <span style="background-color: #0070f3; color: white; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">{item['source']}</span>
            <h3 style="margin: 8px 0 5px 0; color: #333;">{item['title']}</h3>
            <p style="margin: 0 0 5px 0; font-size: 14px; color: #666;">Location: {item['location']}</p>
            <p style="margin: 0 0 10px 0; font-size: 18px; color: #2e7d32; font-weight: bold;">{item['price']}</p>
            <a href="{item['link']}" target="_blank" style="background-color: #2e7d32; color: white; text-decoration: none; padding: 8px 12px; border-radius: 5px; font-weight: bold; display: inline-block;">View Listing</a>
        </div>
        """

    html_content = f"""
    <html>
      <body>
        <h2>🎯 New Golf Cart Matches</h2>
        {html_items}
      </body>
    </html>
    """

    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, [RECIPIENT_EMAIL], msg.as_string())
        print(f"Email alert sent successfully for {len(new_matches)} golf cart match(es)!")
    except Exception as e:
        print(f"Failed to send email alert: {e}")

# ----------------- MAIN RUNNER -----------------
def main():
    seen_ids = load_seen_ids()
    all_new_matches = []

    # 1. Scrape Nextdoor
    nd_matches = scrape_nextdoor(seen_ids)
    all_new_matches.extend(nd_matches)
    for m in nd_matches:
        seen_ids.add(m["id"])

    # 2. Scrape OfferUp
    ou_matches = scrape_offerup(seen_ids)
    all_new_matches.extend(ou_matches)
    for m in ou_matches:
        seen_ids.add(m["id"])

    # 3. Save seen IDs and send email if matches exist
    if all_new_matches:
        save_seen_ids(seen_ids)
        print(f"\nScan complete. Total new golf cart matches found: {len(all_new_matches)}")
        send_email_alert(all_new_matches)
    else:
        print("\nScan complete. No new golf cart listings found across Nextdoor or OfferUp.")

if __name__ == "__main__":
    main()