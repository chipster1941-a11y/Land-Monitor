import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from playwright.sync_api import sync_playwright

# ==========================================
# CONFIGURATION
# ==========================================
TARGET_COUNTIES = ["barron", "polk", "dunn"]
KEY_WORDS = ["cabin", "cottage", "lake", "waterfront", "lakefront", "river", "shore"]
MAX_CABIN_PRICE = 300000  # $300,000 maximum budget cap for cabins

# Craigslist subdomains covering Barron, Polk, & Dunn
CRAIGSLIST_SITES = [
    {"name": "Eau Claire", "url": "https://eauclaire.craigslist.org/search/rea?query=cabin"},
    {"name": "Minneapolis / St. Paul", "url": "https://minneapolis.craigslist.org/search/rea?query=cabin"}
]

# Realtor.com county search URLs filtered for waterfront single-family homes
REALTOR_URLS = [
    {"county": "Barron County", "url": "https://www.realtor.com/realestateandhomes-search/Barron-County_WI/type-single-family-home/with_waterfront"},
    {"county": "Polk County", "url": "https://www.realtor.com/realestateandhomes-search/Polk-County_WI/type-single-family-home/with_waterfront"},
    {"county": "Dunn County", "url": "https://www.realtor.com/realestateandhomes-search/Dunn-County_WI/type-single-family-home/with_waterfront"}
]

SEEN_FILE = "seen_cabins.txt"

# Environment variables for email
EMAIL_SENDER = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")


# ==========================================
# PRICE PARSER HELPER
# ==========================================
def parse_price(price_str):
    """Converts price strings like '$249,900' or '$280k' to numeric floats for budget checks."""
    if not price_str or price_str.upper() in ["N/A", "CHECK LISTING", "CONTACT AGENT"]:
        return None
    cleaned = re.sub(r'[^\d.]', '', price_str.split()[0] if price_str.split() else price_str)
    try:
        val = float(cleaned)
        if 'k' in price_str.lower() and val < 1000:
            val *= 1000
        return val
    except ValueError:
        return None


# ==========================================
# SEEN ID MANAGEMENT
# ==========================================
def load_seen_ids():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen_id(post_id):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{post_id}\n")


# ==========================================
# SCRAPER 1: CRAIGSLIST
# ==========================================
def scrape_craigslist(seen_ids):
    print("[1/2] Checking Craigslist for Lake Cabins...")
    matches = []

    for site in CRAIGSLIST_SITES:
        print(f" -> Querying {site['name']} Craigslist...")
        try:
            res = curl_requests.get(site["url"], impersonate="chrome120", timeout=15)
            if res.status_code != 200:
                print(f"Craigslist ({site['name']}) returned status {res.status_code}")
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            postings = soup.find_all("li", class_="cl-search-result")

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

                full_id = f"cl_cabin_{post_id}"
                if full_id in seen_ids:
                    continue

                title_elem = post.find("a", class_="title") or post.find("a", class_="posting-title")
                if not title_elem:
                    title_elem = post.find("a")
                title = title_elem.text.strip() if title_elem else "WI Lake Cabin"
                link = title_elem["href"] if title_elem and title_elem.has_attr("href") else ""

                price_elem = post.find("span", class_="price") or post.find("span", class_="property-price")
                price = price_elem.text.strip() if price_elem else "N/A"

                # Filter by max cabin price ($300,000)
                num_price = parse_price(price)
                if num_price is not None and num_price > MAX_CABIN_PRICE:
                    continue

                text_to_check = (title + " " + link).lower()
                has_keyword = any(kw in text_to_check for kw in KEY_WORDS)
                has_county = any(c in text_to_check for c in TARGET_COUNTIES)

                if has_keyword or has_county:
                    item = {
                        "id": full_id,
                        "title": title,
                        "price": price,
                        "location": f"WI Cabin ({site['name']})",
                        "link": link,
                        "source": "Craigslist"
                    }
                    matches.append(item)
                    seen_ids.add(full_id)
                    save_seen_id(full_id)

        except Exception as e:
            print(f"Error scraping Craigslist {site['name']}: {e}")

    return matches


# ==========================================
# SCRAPER 2: REALTOR.COM (PLAYWRIGHT)
# ==========================================
def scrape_realtor(seen_ids):
    print("[2/2] Checking Realtor.com via Playwright...")
    matches = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            for item in REALTOR_URLS:
                county = item["county"]
                url = item["url"]
                print(f" -> Scanning {county} waterfront listings...")

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)

                    soup = BeautifulSoup(page.content(), "html.parser")
                    cards = soup.select('div[id^="card-"]') or soup.select('li[data-testid="result-card"]') or soup.select('div[data-testid="property-card"]')

                    for card in cards:
                        card_id = card.get("id") or card.get("data-property-id")
                        if not card_id:
                            continue

                        full_id = f"realtor_{card_id}"
                        if full_id in seen_ids:
                            continue

                        link_elem = card.select_one('a[href*="/realestateandhomes-detail/"]') or card.select_one('a[aria-label]')
                        if not link_elem or not link_elem.get("href"):
                            continue

                        rel_href = link_elem["href"]
                        link = f"https://www.realtor.com{rel_href}" if rel_href.startswith("/") else rel_href

                        price_elem = card.select_one('span[data-label="pc-price"]') or card.select_one('div[data-testid="card-price"]')
                        price = price_elem.text.strip() if price_elem else "N/A"

                        # Filter by max cabin price ($300,000)
                        num_price = parse_price(price)
                        if num_price is not None and num_price > MAX_CABIN_PRICE:
                            continue

                        title_elem = card.select_one('div[data-label="property-address"]') or card.select_one('div[data-testid="card-address"]')
                        title = title_elem.text.strip() if title_elem else f"Waterfront Home in {county}"

                        item_data = {
                            "id": full_id,
                            "title": title,
                            "price": price,
                            "location": f"{county}, WI",
                            "link": link,
                            "source": "Realtor.com"
                        }
                        matches.append(item_data)
                        seen_ids.add(full_id)
                        save_seen_id(full_id)

                except Exception as e:
                    print(f"Error loading Realtor page for {county}: {e}")

            browser.close()

    except Exception as e:
        print(f"Error executing Playwright for Realtor.com: {e}")

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

    subject = f"🏡 Cabin Alert: {first_match['price']} | {first_match['title']}"
    if count > 1:
        subject = f"🏡 {count} New Lake Cabin Listings Found!"

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
        <h2 style="background-color: #1e3a8a; color: white; padding: 12px; border-radius: 6px; text-align: center;">
            🏡 New Lake Cabin Alert ({count})
        </h2>
        <p>The following new lake cabin/waterfront listing(s) under $300,000 were found in Barron, Polk, or Dunn County:</p>
        <hr style="border: 0; border-top: 1px solid #ccc;">
    """

    for item in matches:
        html_content += f"""
        <div style="border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background-color: #f8fafc;">
            <span style="background-color: #0284c7; color: white; padding: 3px 8px; font-size: 12px; border-radius: 4px; font-weight: bold;">
                {item['source']}
            </span>
            <h3 style="margin: 10px 0 5px 0; color: #0f172a;">{item['title']}</h3>
            <p style="margin: 4px 0;"><strong>Price:</strong> <span style="color: #16a34a; font-weight: bold;">{item['price']}</span></p>
            <p style="margin: 4px 0;"><strong>Area:</strong> {item['location']}</p>
            <p style="margin: 12px 0 0 0;">
                <a href="{item['link']}" target="_blank" style="background-color: #2563eb; color: white; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 14px; display: inline-block;">
                    View Listing &rarr;
                </a>
            </p>
        </div>
        """

    html_content += """
        <p style="font-size: 12px; color: #64748b; text-align: center; margin-top: 24px;">
            WI Lake Cabin Monitor • Automated GitHub Action
        </p>
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
        print(f"Email alert sent successfully for {count} cabin match(es)!")
    except Exception as e:
        print(f"Failed to send email alert: {e}")


# ==========================================
# MAIN RUNNER
# ==========================================
def main():
    seen_ids = load_seen_ids()

    all_matches = []
    all_matches.extend(scrape_craigslist(seen_ids))
    all_matches.extend(scrape_realtor(seen_ids))

    print(f"\nScan complete. Total new cabin matches found (under $300,000): {len(all_matches)}")

    if all_matches:
        send_email_alert(all_matches)
    else:
        print("No new lake cabin listings found on this run.")


if __name__ == "__main__":
    main()