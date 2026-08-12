import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from bs4 import BeautifulSoup

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
SEEN_FILE = "seen_properties.txt"
COUNTIES = ["barron", "polk", "dunn", "washburn"]
MIN_ACRES = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/"
}

# Craigslist Search Configs
CRAIGSLIST_TARGETS = [
    {"url": "https://eauclaire.craigslist.org/search/rea?query=barron+dunn", "region": "Barron & Dunn Counties"},
    {"url": "https://minneapolis.craigslist.org/search/rea?query=polk", "region": "Polk County"},
    {"url": "https://rmn.craigslist.org/search/rea?query=washburn", "region": "Washburn County"},
]

# ==========================================
# SEEN ID MANAGEMENT
# ==========================================
def load_seen_ids():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_id(listing_id):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{listing_id}\n")

# ==========================================
# SCRAPER 1: CRAIGSLIST
# ==========================================
def scrape_craigslist(seen_ids):
    print("\n[1/3] Checking Craigslist...")
    matches = []

    for target in CRAIGSLIST_TARGETS:
        try:
            res = requests.get(target["url"], headers=HEADERS, timeout=10)
            if res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            results = soup.select("li.cl-static-search-result") or soup.select("ol.cl-static-search-results li")

            for result in results:
                a_elem = result.select_one("a")
                if not a_elem or not a_elem.get("href"):
                    continue

                link = a_elem.get("href")
                if link.startswith("/"):
                    link = "https://www.craigslist.org" + link

                title_elem = result.select_one(".title") or a_elem
                title = title_elem.text.strip() if title_elem else "No Title"

                post_id_match = re.search(r'/(\d+)\.html', link)
                if not post_id_match:
                    continue
                post_id = f"cl_{post_id_match.group(1)}"

                if post_id in seen_ids:
                    continue

                price_elem = result.select_one(".price")
                loc_elem = result.select_one(".location")

                price = price_elem.text.strip() if price_elem else "N/A"
                location = loc_elem.text.strip() if loc_elem else "WI"

                # Check if it matches target counties or cabin/acreage keywords
                title_lower = title.lower()
                if any(c in title_lower for c in COUNTIES) or any(kw in title_lower for kw in ["cabin", "lake", "acre", "farm", "land"]):
                    item = {
                        "id": post_id,
                        "title": title,
                        "price": price,
                        "location": location,
                        "link": link,
                        "source": "Craigslist"
                    }
                    matches.append(item)
                    seen_ids.add(post_id)
                    save_seen_id(post_id)

        except Exception as e:
            print(f"Error scraping Craigslist target ({target['region']}): {e}")

    return matches

# ==========================================
# SCRAPER 2: LANDWATCH
# ==========================================
def scrape_landwatch(seen_ids):
    print("[2/3] Checking LandWatch...")
    matches = []

    # LandWatch search URL for Wisconsin land
    url = "https://www.landwatch.com/wisconsin-land-for-sale/acres-10-plus"

    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            print(f"LandWatch request returned status {res.status_code}")
            return matches

        soup = BeautifulSoup(res.text, "html.parser")
        listings = soup.select("div[data-record-id]") or soup.select(".listing-card")

        for card in listings:
            card_id = card.get("data-record-id") or card.get("id")
            if not card_id:
                continue

            post_id = f"lw_{card_id}"
            if post_id in seen_ids:
                continue

            title_elem = card.select_one("a[title]") or card.select_one(".title")
            title = title_elem.text.strip() if title_elem else "WI Land Listing"

            link_elem = card.select_one("a[href]")
            link = "https://www.landwatch.com" + link_elem["href"] if link_elem and link_elem["href"].startswith("/") else (link_elem["href"] if link_elem else "")

            price_elem = card.select_one(".price") or card.select_one("[class*='price']")
            price = price_elem.text.strip() if price_elem else "N/A"

            # Check county match
            title_lower = title.lower()
            if any(county in title_lower for county in COUNTIES):
                item = {
                    "id": post_id,
                    "title": title,
                    "price": price,
                    "location": "WI Target County",
                    "link": link,
                    "source": "LandWatch"
                }
                matches.append(item)
                seen_ids.add(post_id)
                save_seen_id(post_id)

    except Exception as e:
        print(f"Error scraping LandWatch: {e}")

    return matches

# ==========================================
# SCRAPER 3: LANDANDFARM
# ==========================================
def scrape_landandfarm(seen_ids):
    print("[3/3] Checking LandAndFarm...")
    matches = []

    url = "https://www.landandfarm.com/search/wisconsin-land-for-sale/"

    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code != 200:
            return matches

        soup = BeautifulSoup(res.text, "html.parser")
        cards = soup.select(".property-card") or soup.select("article")

        for card in cards:
            link_elem = card.select_one("a[href]")
            if not link_elem:
                continue

            link = link_elem["href"]
            if link.startswith("/"):
                link = "https://www.landandfarm.com" + link

            # Extract numeric ID from link
            id_match = re.search(r'/(\d+)/?$', link)
            if not id_match:
                continue

            post_id = f"laf_{id_match.group(1)}"
            if post_id in seen_ids:
                continue

            title_elem = card.select_one(".title") or card.select_one("h2") or link_elem
            title = title_elem.text.strip() if title_elem else "Wisconsin Farm/Land"

            price_elem = card.select_one(".price")
            price = price_elem.text.strip() if price_elem else "N/A"

            title_lower = title.lower()
            if any(county in title_lower for county in COUNTIES):
                item = {
                    "id": post_id,
                    "title": title,
                    "price": price,
                    "location": "WI Target County",
                    "link": link,
                    "source": "LandAndFarm"
                }
                matches.append(item)
                seen_ids.add(post_id)
                save_seen_id(post_id)

    except Exception as e:
        print(f"Error scraping LandAndFarm: {e}")

    return matches

# ==========================================
# EMAIL ALERT FUNCTION
# ==========================================
def send_email_alert(matches):
    sender = os.getenv("SENDER_EMAIL") or os.getenv("EMAIL_SENDER")
    password = os.getenv("SENDER_PASSWORD") or os.getenv("EMAIL_PASSWORD")
    receiver = os.getenv("RECIPIENT_EMAIL") or os.getenv("EMAIL_RECEIVER")

    if not sender or not password or not receiver:
        print("Email credentials missing in environment variables. Skipping email notification.")
        return

    # Build Dynamic Subject Line
    if len(matches) == 1:
        item = matches[0]
        acres_match = re.search(r'(\d+\.?\d*)\s*(?:-\s*)?acres?', item['title'], re.IGNORECASE)
        acres_str = f"{acres_match.group(1)} Acres" if acres_match else "Property"
        subject = f"🌲 WI Alert: {item['price']} | {acres_str} | {item['source']} - {item['title'][:35]}..."
    else:
        prices = [m['price'] for m in matches if m['price'] != "N/A"]
        price_range = f" ({min(prices)} - {max(prices)})" if prices else ""
        subject = f"🌲 WI Alert: {len(matches)} New Property Listings{price_range}"

    # Build HTML Email Body
    html_items = ""
    for item in matches:
        html_items += f"""
        <div style="border: 1px solid #ddd; padding: 15px; margin-bottom: 15px; border-radius: 8px; font-family: sans-serif;">
            <h3 style="margin-top: 0; color: #2c3e50;">{item['title']}</h3>
            <p><strong>Price:</strong> <span style="color: #27ae60; font-size: 1.1em;">{item['price']}</span></p>
            <p><strong>Source:</strong> {item['source']} | <strong>Location:</strong> {item['location']}</p>
            <p><a href="{item['link']}" target="_blank" style="background-color: #2980b9; color: white; padding: 8px 12px; text-decoration: none; border-radius: 4px; display: inline-block;">View Listing</a></p>
        </div>
        """

    html_body = f"""
    <html>
      <body>
        <h2 style="color: #27ae60;">🌲 Wisconsin Property Alert</h2>
        <p>Found <strong>{len(matches)}</strong> new listing(s) matching your criteria:</p>
        {html_items}
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        print(f"Successfully sent email alert! Subject: {subject}")
    except Exception as e:
        print(f"Failed to send email: {e}")

# ==========================================
# MAIN RUNNER
# ==========================================
def main():
    seen_ids = load_seen_ids()

    all_matches = []
    all_matches.extend(scrape_craigslist(seen_ids))
    all_matches.extend(scrape_landwatch(seen_ids))
    all_matches.extend(scrape_landandfarm(seen_ids))

    print(f"\nScan complete. Total new matches found: {len(all_matches)}")

    if all_matches:
        send_email_alert(all_matches)
    else:
        print("No new property listings found on this run.")

if __name__ == "__main__":
    main()