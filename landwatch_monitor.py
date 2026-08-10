import os
import sys
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from curl_cffi import requests

# ==========================================
# --- CONFIGURATION ---
# ==========================================

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "chipster1941@gmail.com")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "ygyk ijmw qowl qpad")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "kwolf100@hotmail.com")

COUNTIES = [
    "barron-county",
    "polk-county",
    "washburn-county",
    "dunn-county",
]

# Filtering Options
MIN_ACRES = None       # e.g., 10 (or None for no limit)
MAX_PRICE = None       # e.g., 1000000 (or None for no limit)
KEYWORD_HIGHLIGHTS = ["tillable", "pasture", "barn", "waterfront", "creek", "hunting", "timber"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

MEMORY_FILE = "seen_farms.txt"


# ==========================================
# --- RICH EMAIL ALERT FUNCTION ---
# ==========================================

def send_rich_email_alert(title, link, county, source_site):
    county_name = county.replace('-', ' ').title()
    
    # Check for keyword highlights in title
    matched_keywords = [kw for kw in KEYWORD_HIGHLIGHTS if kw in title.lower()]
    keyword_badge = f" • Keywords: {', '.join(matched_keywords)}" if matched_keywords else ""

    subject = f"🚨 New {source_site} Listing: {county_name}, WI"
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background-color: #f4f6f8;
                margin: 0;
                padding: 20px;
            }}
            .card {{
                max-width: 600px;
                margin: 0 auto;
                background-color: #ffffff;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                border: 1px solid #e0e0e0;
            }}
            .header {{
                background-color: #2e7d32;
                color: #ffffff;
                padding: 20px;
                text-align: center;
            }}
            .header h2 {{
                margin: 0;
                font-size: 22px;
            }}
            .content {{
                padding: 25px;
            }}
            .badge {{
                display: inline-block;
                padding: 5px 12px;
                border-radius: 15px;
                font-size: 13px;
                font-weight: bold;
                color: #ffffff;
                margin-right: 8px;
            }}
            .badge-source {{ background-color: #1565c0; }}
            .badge-county {{ background-color: #e65100; }}
            .title {{
                font-size: 18px;
                color: #333333;
                margin-top: 15px;
                margin-bottom: 20px;
                line-height: 1.4;
            }}
            .button-container {{
                text-align: center;
                margin-top: 25px;
            }}
            .btn {{
                background-color: #2e7d32;
                color: #ffffff !important;
                padding: 12px 25px;
                text-decoration: none;
                border-radius: 5px;
                font-weight: bold;
                display: inline-block;
            }}
            .footer {{
                background-color: #f9f9f9;
                padding: 15px;
                text-align: center;
                font-size: 12px;
                color: #777777;
                border-top: 1px solid #eeeeee;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="header">
                <h2>🌾 New Farmland Opportunity Found!</h2>
            </div>
            <div class="content">
                <div>
                    <span class="badge badge-source">{source_site}</span>
                    <span class="badge badge-county">{county_name}, WI</span>
                </div>
                <div class="title">
                    <strong>Listing:</strong> {title}{keyword_badge}
                </div>
                <div class="button-container">
                    <a href="{link}" class="btn" target="_blank">View Listing Details ➔</a>
                </div>
            </div>
            <div class="footer">
                Automated Farmland Monitor • Checking Barron, Polk, Washburn & Dunn Counties
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print(f"  📧 Rich email notification sent for: {title}")
    except Exception as e:
        print(f"  ⚠️ Failed to send email: {e}")


def send_weekly_digest(seen_links):
    subject = f"📊 Weekly Farmland Monitor Summary Digest ({len(seen_links)} Total Cataloged)"
    
    links_list = "".join([f"<li><a href='{link}'>{link}</a></li>" for link in list(seen_links)[:50]])
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2>🌾 Weekly Farmland Digest Summary</h2>
        <p>Your automated monitor is actively watching Barron, Polk, Washburn, and Dunn counties in Wisconsin.</p>
        <p><strong>Total Cataloged Properties in Memory:</strong> {len(seen_links)}</p>
        <h3>Recent Tracked Property Links:</h3>
        <ul>{links_list}</ul>
        <hr>
        <p style="font-size: 12px; color: #777;">Farmland Monitor Weekly Digest</p>
    </body>
    </html>
    """

    msg = MIMEMultipart('alternative')
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print(f"  📧 Weekly digest sent successfully!")
    except Exception as e:
        print(f"  ⚠️ Failed to send weekly digest: {e}")


# ==========================================
# --- HELPER LOGIC & FILTERS ---
# ==========================================

def load_seen_properties():
    try:
        with open(MEMORY_FILE, "r") as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()


def save_seen_property(link):
    with open(MEMORY_FILE, "a") as f:
        f.write(link + "\n")


# ==========================================
# --- SITE SCRAPERS ---
# ==========================================

def check_landwatch_for_county(county_slug, seen_properties):
    county_name = county_slug.replace('-', ' ').title()
    county_raw = county_slug.split('-')[0]
    url = f"https://www.landwatch.com/wisconsin-land-for-sale/{county_slug}/farms-ranches"
    print(f"🔍 Searching LandWatch in [{county_name}]...")

    try:
        response = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
        if response.status_code != 200:
            print(f"⚠️ Failed to reach LandWatch for {county_slug}. Status: {response.status_code}")
            return
    except Exception as e:
        print(f"⚠️ Connection error for LandWatch ({county_slug}): {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    all_links = soup.find_all("a", href=True)
    new_found_count = 0

    for a_tag in all_links:
        href = a_tag["href"]
        is_property_link = any(pattern in href for pattern in ["/pid/", "/land-for-sale/", "/farms-ranches/"])
        is_wisconsin = "wisconsin" in href.lower() or "wi" in href.lower()
        is_not_search_page = not href.endswith("/farms-ranches") and not href.endswith("/land-for-sale")
        
        title = a_tag.text.strip() or f"Farmland Listing in {county_name}"
        title = " ".join(title.split())
        is_correct_county = county_raw in href.lower() or county_raw in title.lower()

        if is_property_link and is_wisconsin and is_not_search_page and is_correct_county:
            full_url = href if href.startswith("http") else f"https://www.landwatch.com{href}"

            if full_url not in seen_properties:
                print(f"\n🚨 NEW LANDWATCH LISTING IN {county_slug.upper()}!")
                send_rich_email_alert(title, full_url, county_slug, "LandWatch")
                save_seen_property(full_url)
                seen_properties.add(full_url)
                new_found_count += 1

    if new_found_count == 0:
        print(f"  No new LandWatch listings in {county_name}.")


def check_landandfarm_for_county(county_slug, seen_properties):
    county_name = county_slug.replace('-', ' ').title()
    county_raw = county_slug.split('-')[0]
    url = f"https://www.landandfarm.com/wisconsin-land-for-sale/{county_slug}/farms-ranches/"
    print(f"🔍 Searching LandAndFarm in [{county_name}]...")

    try:
        response = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
        if response.status_code != 200:
            print(f"⚠️ Failed to reach LandAndFarm for {county_slug}. Status: {response.status_code}")
            return
    except Exception as e:
        print(f"⚠️ Connection error for LandAndFarm ({county_slug}): {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    all_links = soup.find_all("a", href=True)
    new_found_count = 0

    for a_tag in all_links:
        href = a_tag["href"]
        is_property_link = any(pattern in href for pattern in ["/property/", "/pid/"])
        is_wisconsin = "wisconsin" in href.lower() or "wi" in href.lower()

        title = a_tag.text.strip() or f"LandAndFarm Listing in {county_name}"
        title = " ".join(title.split())
        is_correct_county = county_raw in href.lower() or county_raw in title.lower()

        if is_property_link and is_wisconsin and is_correct_county:
            full_url = href if href.startswith("http") else f"https://www.landandfarm.com{href}"

            if full_url not in seen_properties:
                print(f"\n🚨 NEW LANDANDFARM LISTING IN {county_slug.upper()}!")
                send_rich_email_alert(title, full_url, county_slug, "LandAndFarm")
                save_seen_property(full_url)
                seen_properties.add(full_url)
                new_found_count += 1

    if new_found_count == 0:
        print(f"  No new LandAndFarm listings in {county_name}.")


def check_land_com_for_county(county_slug, seen_properties):
    county_name = county_slug.replace('-', ' ').title()
    county_raw = county_slug.split('-')[0]
    url = f"https://www.land.com/wisconsin-land-for-sale/{county_slug}/farms-ranches/"
    print(f"🔍 Searching Land.com in [{county_name}]...")

    try:
        response = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
        if response.status_code != 200:
            print(f"⚠️ Failed to reach Land.com for {county_slug}. Status: {response.status_code}")
            return
    except Exception as e:
        print(f"⚠️ Connection error for Land.com ({county_slug}): {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    all_links = soup.find_all("a", href=True)
    new_found_count = 0

    for a_tag in all_links:
        href = a_tag["href"]
        is_property_link = any(pattern in href for pattern in ["/property/", "/pid/"])
        is_wisconsin = "wisconsin" in href.lower() or "wi" in href.lower()

        title = a_tag.text.strip() or f"Land.com Listing in {county_name}"
        title = " ".join(title.split())
        is_correct_county = county_raw in href.lower() or county_raw in title.lower()

        if is_property_link and is_wisconsin and is_correct_county:
            full_url = href if href.startswith("http") else f"https://www.land.com{href}"

            if full_url not in seen_properties:
                print(f"\n🚨 NEW LAND.COM LISTING IN {county_slug.upper()}!")
                send_rich_email_alert(title, full_url, county_slug, "Land.com")
                save_seen_property(full_url)
                seen_properties.add(full_url)
                new_found_count += 1

    if new_found_count == 0:
        print(f"  No new Land.com listings in {county_name}.")


def check_whitetail_properties(county_slug, seen_properties):
    county_name = county_slug.replace('-', ' ').title()
    county_raw = county_slug.split('-')[0]
    url = f"https://www.whitetailproperties.com/properties/wisconsin/{county_slug}"
    print(f"🔍 Searching Whitetail Properties in [{county_name}]...")

    try:
        response = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
        if response.status_code != 200:
            print(f"  No direct page or status {response.status_code} for Whitetail Properties in {county_name}.")
            return
    except Exception as e:
        print(f"⚠️ Connection error for Whitetail Properties ({county_slug}): {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    all_links = soup.find_all("a", href=True)
    new_found_count = 0

    for a_tag in all_links:
        href = a_tag["href"]
        if "/properties/" in href and "wisconsin" in href.lower():
            full_url = href if href.startswith("http") else f"https://www.whitetailproperties.com{href}"
            title = a_tag.text.strip() or f"Whitetail Properties Listing in {county_name}"
            title = " ".join(title.split())

            if full_url not in seen_properties:
                print(f"\n🚨 NEW WHITETAIL PROPERTIES LISTING IN {county_slug.upper()}!")
                send_rich_email_alert(title, full_url, county_slug, "Whitetail Properties")
                save_seen_property(full_url)
                seen_properties.add(full_url)
                new_found_count += 1

    if new_found_count == 0:
        print(f"  No new Whitetail Properties listings in {county_name}.")


def main():
    seen_properties = load_seen_properties()
    
    # Check if triggered for weekly digest summary
    if len(sys.argv) > 1 and sys.argv[1] == "--weekly-digest":
        print("📊 Running Weekly Digest Mode...")
        send_weekly_digest(seen_properties)
        return

    # Standard Scraper Loop
    for county in COUNTIES:
        check_landwatch_for_county(county, seen_properties)
        time.sleep(2)
        check_landandfarm_for_county(county, seen_properties)
        time.sleep(2)
        check_land_com_for_county(county, seen_properties)
        time.sleep(2)
        check_whitetail_properties(county, seen_properties)
        time.sleep(2)
        
    print("\nCheck complete!")


if __name__ == "__main__":
    main()