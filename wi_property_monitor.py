import os
import re
import csv
import json
import smtplib
import urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from playwright.sync_api import sync_playwright

# ==========================================
# CONFIGURATION
# ==========================================
TARGET_COUNTIES = ["barron", "polk", "dunn"]
KEY_WORDS = ["land", "acre", "acres", "acreage", "lot", "parcel", "waterfront", "river", "farm", "farmland"]
MAX_LAND_PRICE = 200000 

# UPDATED: Changed category code from /search/rea to /search/repa
CRAIGSLIST_SITES = [
    {"name": "Eau Claire", "url": "https://eauclaire.craigslist.org/search/eau/rea?query=land"},
    {"name": "Minneapolis / St. Paul", "url": "https://minneapolis.craigslist.org/search/msp/rea?query=land"}
]

REALTOR_URLS = [
    {"county": "Barron County", "url": "https://www.realtor.com/realestateandhomes-search/Barron-County_WI/type-land/price-na-200000"},
    {"county": "Polk County", "url": "https://www.realtor.com/realestateandhomes-search/Polk-County_WI/type-land/price-na-200000"},
    {"county": "Dunn County", "url": "https://www.realtor.com/realestateandhomes-search/Dunn-County_WI/type-land/price-na-200000"}
]

SEEN_FILE = "seen_properties.txt"
CSV_FILE = "matched_properties.csv"

EMAIL_SENDER = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")


# ==========================================
# HELPERS
# ==========================================
def parse_price(price_str, acreage=None):
    if not price_str or any(w in str(price_str).upper() for w in ["N/A", "CHECK LISTING", "CONTACT AGENT"]):
        return None
    
    lowered = str(price_str).lower()
    
    if "auction" in lowered or "starting bid" in lowered:
        return None

    match = re.search(r'\$?\s*([\d,]+(?:\.\d+)?)', str(price_str))
    if not match:
        return None
        
    cleaned = match.group(1).replace(',', '')
    
    try:
        val = float(cleaned)
        if ('m' in lowered or 'million' in lowered) and val < 1000:
            val *= 1_000_000
        elif ('k' in lowered or 'thousand' in lowered) and val < 1000:
            val *= 1_000

        if ("/acre" in lowered or "per acre" in lowered) and acreage:
            val *= acreage
            
        return val
    except ValueError:
        return None


def load_seen_ids():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen_id(post_id):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{post_id}\n")


def save_to_csv(matches, filename=CSV_FILE):
    if not matches:
        return

    file_exists = os.path.exists(filename)
    fieldnames = ["date_found", "source", "price", "title", "location", "link", "id"]

    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in matches:
            writer.writerow({
                "date_found": today_str,
                "source": item.get("source", ""),
                "price": item.get("price", ""),
                "title": item.get("title", ""),
                "location": item.get("location", ""),
                "link": item.get("link", ""),
                "id": item.get("id", "")
            })
    print(f"Logged {len(matches)} property listing(s) to {filename}")


# ==========================================
# SCRAPER 1: CRAIGSLIST
# ==========================================
def scrape_craigslist(seen_ids):
    print("[1/2] Checking Craigslist for Land/Acreage...")
    matches = []

    for site in CRAIGSLIST_SITES:
        print(f" -> Querying {site['name']} Craigslist...")
        try:
            res = curl_requests.get(site["url"], impersonate="chrome120", timeout=15)
            if res.status_code != 200:
                print(f"    [ERROR] Craigslist HTTP Status Code: {res.status_code}")
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            postings = soup.find_all("li", class_="cl-search-result")
            print(f"    Raw postings returned by Craigslist: {len(postings)}")

            filtered_out_already_seen = 0
            filtered_out_price = 0

            for post in postings:
                post_id = post.get("data-pid")
                if not post_id:
                    a_tag = post.find("a", href=True)
                    if a_tag:
                        match = re.search(r"/(\d+)\.html", a_tag["href"])
                        if match:
                            post_id = match.group(1)

                if not post_id:
                    continue

                full_id = f"cl_land_{post_id}"
                if full_id in seen_ids:
                    filtered_out_already_seen += 1
                    continue

                title_elem = post.find("a", class_="title") or post.find("a", class_="posting-title") or post.find("a")
                title = title_elem.text.strip() if title_elem else "WI Land Parcel"
                link = title_elem["href"] if title_elem and title_elem.has_attr("href") else ""

                price_elem = post.find("span", class_="price") or post.find("span", class_="property-price")
                price = price_elem.text.strip() if price_elem else "N/A"

                num_price = parse_price(price)
                if num_price is not None and num_price > MAX_LAND_PRICE:
                    filtered_out_price += 1
                    continue

                # Broadened match logic
                img_elem = post.find("img")
                image_url = img_elem["src"] if img_elem and img_elem.has_attr("src") else ""

                search_query = urllib.parse.quote(f"{title}, {site['name']} WI")
                map_url = f"https://www.google.com/maps/search/?api=1&query={search_query}"

                item = {
                    "id": full_id,
                    "title": title,
                    "price": price,
                    "location": f"WI Land ({site['name']})",
                    "link": link,
                    "source": "Craigslist",
                    "image_url": image_url,
                    "map_url": map_url
                }
                matches.append(item)
                seen_ids.add(full_id)
                save_seen_id(full_id)

            print(f"    Summary for {site['name']}: {len(matches)} new matches | {filtered_out_already_seen} already in seen_properties.txt | {filtered_out_price} over max budget")

        except Exception as e:
            print(f"    [ERROR] Craigslist exception on {site['name']}: {e}")

    return matches


# ==========================================
# SCRAPER 2: REALTOR.COM
# ==========================================
def scrape_realtor(seen_ids):
    print("[2/2] Checking Realtor.com via Playwright...")
    matches = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-infobars",
                    "--window-position=0,0",
                    "--ignore-certificate-errors",
                    "--ignore-certificate-errors-spki-list"
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"'
                }
            )
            page = context.new_page()
            
            # Remove navigator.webdriver flag
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            for item in REALTOR_URLS:
                county = item["county"]
                url = item["url"]
                print(f" -> Scanning {county} land listings...")

                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)

                    content_text = page.content()
                    
                    # Bot check detector
                    if "captcha" in content_text.lower() or "datadome" in content_text.lower() or "access denied" in content_text.lower():
                        print(f"    [WARNING] Realtor.com blocked GitHub Action runner IP for {county}!")
                        continue

                    soup = BeautifulSoup(content_text, "html.parser")
                    
                    found_via_json = False
                    script_tag = soup.find("script", id="__NEXT_DATA__")
                    
                    if script_tag and script_tag.string:
                        try:
                            data = json.loads(script_tag.string)
                            props = (
                                data.get("props", {}).get("pageProps", {}).get("properties", [])
                                or data.get("props", {}).get("pageProps", {}).get("searchResults", {}).get("home", {}).get("results", [])
                            )
                            print(f"    JSON Hydration found {len(props)} listings for {county}")

                            for prop in props:
                                prop_id = prop.get("property_id") or prop.get("mls", {}).get("id")
                                if not prop_id:
                                    continue

                                full_id = f"realtor_{prop_id}"
                                if full_id in seen_ids:
                                    continue

                                raw_price = prop.get("list_price") or prop.get("price") or 0
                                if raw_price and float(raw_price) > MAX_LAND_PRICE:
                                    continue

                                price_str = f"${raw_price:,.0f}" if raw_price else "N/A"
                                addr = prop.get("location", {}).get("address", {})
                                title = addr.get("line") or f"Land Lot in {county}"
                                city = addr.get("city", "")
                                state = addr.get("state_code", "WI")
                                full_location = f"{city}, {state}" if city else f"{county}, WI"

                                rel_permalink = prop.get("permalink", "")
                                link = f"https://www.realtor.com/realestateandhomes-detail/{rel_permalink}" if rel_permalink else url
                                img_url = prop.get("primary_photo", {}).get("href", "")

                                search_query = urllib.parse.quote(f"{title}, {full_location}")
                                map_url = f"https://www.google.com/maps/search/?api=1&query={search_query}"

                                item_data = {
                                    "id": full_id,
                                    "title": title,
                                    "price": price_str,
                                    "location": full_location,
                                    "link": link,
                                    "source": "Realtor.com",
                                    "image_url": img_url,
                                    "map_url": map_url
                                }
                                matches.append(item_data)
                                seen_ids.add(full_id)
                                save_seen_id(full_id)
                                found_via_json = True

                        except Exception as json_err:
                            print(f"    JSON parse fallback triggered for {county}: {json_err}")

                    if not found_via_json:
                        cards = soup.select('div[id^="card-"]') or soup.select('li[data-testid="result-card"]') or soup.select('div[data-testid="property-card"]')
                        print(f"    DOM CSS Selectors found {len(cards)} listing cards for {county}")

                except Exception as e:
                    print(f"    [ERROR] Page navigation failed for {county}: {e}")

            browser.close()

    except Exception as e:
        print(f"    [ERROR] Playwright setup failed: {e}")

    return matches


# ==========================================
# MAIN RUNNER
# ==========================================
def main():
    seen_ids = load_seen_ids()

    all_matches = []
    all_matches.extend(scrape_craigslist(seen_ids))
    all_matches.extend(scrape_realtor(seen_ids))

    print(f"\nScan complete. Total new land matches found: {len(all_matches)}")

    if all_matches:
        save_to_csv(all_matches, CSV_FILE)
        send_email_alert(all_matches)
    else:
        print("No new land listings found on this run.")


if __name__ == "__main__":
    main()