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

# ==========================================
# CONFIGURATION
# ==========================================
TARGET_COUNTIES = ["barron", "polk", "dunn"]
KEY_WORDS = ["land", "acre", "acres", "acreage", "lot", "parcel", "waterfront", "river", "farm", "farmland"]
MAX_LAND_PRICE = 300000  # $300,000 max budget

# 1. CRAIGSLIST (Vacant Land Category)
CRAIGSLIST_SITES = [
    {"name": "Eau Claire", "url": "https://eauclaire.craigslist.org/search/rva?query=land"},
    {"name": "Minneapolis / St. Paul", "url": "https://minneapolis.craigslist.org/search/rva?query=land"}
]

# 2. LANDWATCH (County Search Endpoints with price range slug)
LANDWATCH_URLS = [
    {"county": "Barron County", "url": "https://www.landwatch.com/wisconsin-land-for-sale/barron-county/price-under-299999"},
    {"county": "Polk County", "url": "https://www.landwatch.com/wisconsin-land-for-sale/polk-county/price-under-299999"},
    {"county": "Dunn County", "url": "https://www.landwatch.com/wisconsin-land-for-sale/dunn-county/price-under-299999"}
]

SEEN_FILE = "seen_properties.txt"
CSV_FILE = "matched_properties.csv"

EMAIL_SENDER = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")


# ==========================================
# HELPERS
# ==========================================
def parse_price(price_str):
    if not price_str or any(w in str(price_str).upper() for w in ["N/A", "CHECK LISTING", "CONTACT AGENT"]):
        return None
    
    match = re.search(r'\$?\s*([\d,]+(?:\.\d+)?)', str(price_str))
    if not match:
        return None
        
    try:
        return float(match.group(1).replace(',', ''))
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
    print("[1/2] Checking Craigslist Vacant Land...")
    matches = []

    for site in CRAIGSLIST_SITES:
        print(f" -> Querying {site['name']} Craigslist...")
        try:
            res = curl_requests.get(site["url"], impersonate="chrome120", timeout=15)
            if res.status_code != 200:
                print(f"    [ERROR] HTTP Status Code: {res.status_code}")
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            postings = soup.find_all("li", class_="cl-search-result") or soup.find_all("li", class_="result-row")

            filtered_seen = 0
            filtered_price = 0
            filtered_county = 0

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
                    filtered_seen += 1
                    continue

                title_elem = post.find("a", class_="title") or post.find("a", class_="posting-title") or post.find("a")
                title = title_elem.text.strip() if title_elem else "WI Land Parcel"
                link = title_elem["href"] if title_elem and title_elem.has_attr("href") else ""

                # County filter for Craigslist
                text_to_check = (title + " " + link).lower()
                has_county = any(c in text_to_check for c in TARGET_COUNTIES)
                if not has_county:
                    filtered_county += 1
                    continue

                price_elem = post.find("span", class_="price") or post.find("span", class_="property-price")
                price = price_elem.text.strip() if price_elem else "N/A"

                num_price = parse_price(price)
                if num_price is not None and num_price > MAX_LAND_PRICE:
                    filtered_price += 1
                    continue

                img_elem = post.find("img")
                image_url = img_elem["src"] if img_elem and img_elem.has_attr("src") else ""

                search_query = urllib.parse.quote(f"{title}, WI")
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

            print(f"    New matches: {len(matches)} | Filtered Out: {filtered_county} non-target counties, {filtered_seen} already seen, {filtered_price} over $300k")

        except Exception as e:
            print(f"    [ERROR] Exception on {site['name']}: {e}")

    return matches


# ==========================================
# SCRAPER 2: LANDWATCH
# ==========================================
def scrape_landwatch(seen_ids):
    print("[2/2] Checking LandWatch for County Farmland/Acreage...")
    matches = []

    for item in LANDWATCH_URLS:
        county = item["county"]
        url = item["url"]
        print(f" -> Querying LandWatch for {county} (Under $300k)...")

        try:
            res = curl_requests.get(url, impersonate="chrome120", timeout=15)
            if res.status_code != 200:
                print(f"    [ERROR] LandWatch HTTP Status Code for {county}: {res.status_code}")
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            
            # 1. Parse JSON-LD metadata for structured properties
            found_via_json = False
            json_scripts = soup.find_all("script", type="application/ld+json")
            for script in json_scripts:
                if not script.string:
                    continue
                try:
                    data = json.loads(script.string)
                    items = []
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = data.get("itemListElement", [data])

                    for entry in items:
                        item_obj = entry.get("item", entry) if isinstance(entry, dict) else {}
                        if item_obj.get("@type") not in ["SingleFamilyResidence", "Product", "Place", "Offer"]:
                            continue

                        link = item_obj.get("url") or item_obj.get("mainEntityOfPage") or ""
                        if not link or "/wisconsin-land-for-sale/" not in link:
                            continue

                        # Extract property ID
                        id_match = re.search(r'/pid/(\d+)', link) or re.search(r'/id/(\d+)', link) or re.search(r'-(\d+)$', link)
                        prop_id = id_match.group(1) if id_match else re.sub(r'[^a-zA-Z0-9]', '', link.split("?")[0])

                        full_id = f"landwatch_{prop_id}"
                        if full_id in seen_ids:
                            continue

                        title = item_obj.get("name") or f"Land Lot in {county}"
                        
                        # Price extraction from JSON-LD
                        offers = item_obj.get("offers", {})
                        raw_price = offers.get("price") if isinstance(offers, dict) else None
                        
                        if raw_price:
                            num_price = float(raw_price)
                            price_str = f"${num_price:,.0f}"
                        else:
                            price_str = "N/A"
                            num_price = None

                        if num_price is not None and num_price > MAX_LAND_PRICE:
                            continue

                        img_url = item_obj.get("image") or ""
                        if isinstance(img_url, list) and len(img_url) > 0:
                            img_url = img_url[0]

                        search_query = urllib.parse.quote(f"{title}, {county}, WI")
                        map_url = f"https://www.google.com/maps/search/?api=1&query={search_query}"

                        item_data = {
                            "id": full_id,
                            "title": title[:80],
                            "price": price_str,
                            "location": f"{county}, WI",
                            "link": link,
                            "source": "LandWatch",
                            "image_url": img_url if isinstance(img_url, str) else "",
                            "map_url": map_url
                        }
                        matches.append(item_data)
                        seen_ids.add(full_id)
                        save_seen_id(full_id)
                        found_via_json = True

                except Exception:
                    continue

            # 2. Fallback: Parse card blocks directly if JSON-LD isn't present
            if not found_via_json:
                cards = soup.select('a[href*="/wisconsin-land-for-sale/"]')
                for card in cards:
                    href = card.get("href", "")
                    if not href or "/wisconsin-land-for-sale/" not in href:
                        continue

                    link = f"https://www.landwatch.com{href}" if href.startswith("/") else href
                    card_text = card.get_text(" ", strip=True)

                    price_match = re.search(r'\$[\d,]+', card_text)
                    if not price_match:
                        continue

                    price_str = price_match.group(0)
                    num_price = parse_price(price_str)

                    if num_price is None or num_price > MAX_LAND_PRICE:
                        continue

                    id_match = re.search(r'/pid/(\d+)', href) or re.search(r'/id/(\d+)', href) or re.search(r'-(\d+)$', href)
                    prop_id = id_match.group(1) if id_match else re.sub(r'[^a-zA-Z0-9]', '', href.split("?")[0])

                    full_id = f"landwatch_{prop_id}"
                    if full_id in seen_ids:
                        continue

                    title = re.sub(r'\s+', ' ', card_text)[:80]
                    img_elem = card.find("img")
                    image_url = (img_elem.get("src") or img_elem.get("data-src") or "") if img_elem else ""

                    search_query = urllib.parse.quote(f"{title}, {county}, WI")
                    map_url = f"https://www.google.com/maps/search/?api=1&query={search_query}"

                    item_data = {
                        "id": full_id,
                        "title": title,
                        "price": price_str,
                        "location": f"{county}, WI",
                        "link": link,
                        "source": "LandWatch",
                        "image_url": image_url,
                        "map_url": map_url
                    }
                    matches.append(item_data)
                    seen_ids.add(full_id)
                    save_seen_id(full_id)

            print(f"    New LandWatch matches found for {county}: {len(matches)}")

        except Exception as e:
            print(f"    [ERROR] LandWatch exception for {county}: {e}")

    return matches


# ==========================================
# EMAIL NOTIFICATIONS
# ==========================================
def send_email_alert(matches):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email credentials missing. Skipping email send.")
        return

    count = len(matches)
    first_match = matches[0]

    subject = f"🌾 {count} New Land Listings Found!" if count > 1 else f"🌾 Land Alert: {first_match['price']} | {first_match['title']}"

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f1f5f9; padding: 20px 10px; color: #333;">
        <div style="max-width: 600px; margin: auto; background-color: #ffffff; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.08);">
            <h2 style="background-color: #15803d; color: white; padding: 14px; border-radius: 6px; text-align: center; margin-top: 0;">
                🌾 New Land / Acreage Alert ({count})
            </h2>
            <p style="color: #475569; font-size: 14px;">The following new land listing(s) under $300,000 were found:</p>
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin-bottom: 20px;">
    """

    for item in matches:
        img_src = item.get("image_url") or "https://via.placeholder.com/150?text=No+Photo"

        html_content += f"""
        <div style="border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 20px; background-color: #f8fafc;">
            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                    <td width="130" valign="top" style="padding-right: 14px;">
                        <img src="{img_src}" alt="Property" width="120" height="90" style="border-radius: 6px; object-fit: cover; display: block; border: 1px solid #cbd5e1;">
                    </td>
                    <td valign="top">
                        <span style="background-color: #16a34a; color: white; padding: 2px 8px; font-size: 11px; border-radius: 4px; font-weight: bold; text-transform: uppercase;">
                            {item['source']}
                        </span>
                        <h3 style="margin: 6px 0 4px 0; color: #0f172a; font-size: 16px; line-height: 1.2;">{item['title']}</h3>
                        <p style="margin: 2px 0; font-size: 14px;"><strong>Price:</strong> <span style="color: #16a34a; font-weight: bold;">{item['price']}</span></p>
                        <p style="margin: 2px 0; font-size: 13px; color: #64748b;"><strong>Area:</strong> {item['location']}</p>
                    </td>
                </tr>
            </table>

            <div style="margin-top: 14px; padding-top: 12px; border-top: 1px solid #e2e8f0; display: flex; gap: 8px;">
                <a href="{item['link']}" target="_blank" style="background-color: #15803d; color: white; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 13px; font-weight: bold;">
                    View Listing &rarr;
                </a>
                <a href="{item['map_url']}" target="_blank" style="background-color: #475569; color: white; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 13px;">
                    📍 Google Maps
                </a>
            </div>
        </div>
        """

    html_content += """
            <p style="font-size: 12px; color: #94a3b8; text-align: center; margin-top: 24px;">
                WI Land Property Monitor • Automated GitHub Action
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
        print(f"Email alert sent successfully for {count} land match(es)!")
    except Exception as e:
        print(f"Failed to send email alert: {e}")