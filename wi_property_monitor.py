import os
import re
import smtplib
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==========================================
# CONFIGURATION
# ==========================================
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_SENDER = os.getenv("EMAIL_SENDER")      # Your Gmail address
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")  # Your Gmail App Password
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")  # Where alerts are sent

SEEN_FILE = "seen_properties.txt"

# Targeted Craigslist Search Endpoints
SEARCH_TARGETS = [
    # --- FARMLAND & ACREAGE (cat=laa -> Land for Sale) ---
    {
        "type": "Farmland",
        "region": "Polk County Area",
        "url": "https://www.craigslist.org/search/area/minneapolis?cat=laa&query=land+acre"
    },
    {
        "type": "Farmland",
        "region": "Barron & Dunn Counties Area",
        "url": "https://www.craigslist.org/search/area/eauclaire?cat=laa&query=land+acre"
    },
    {
        "type": "Farmland",
        "region": "Washburn County Area",
        "url": "https://www.craigslist.org/search/area/duluth?cat=laa&query=land+acre"
    },

    # --- LAKE CABINS & REAL ESTATE (cat=rea -> Real Estate) ---
    {
        "type": "Cabin",
        "region": "Polk County / St. Croix Valley",
        "url": "https://www.craigslist.org/search/area/minneapolis?cat=rea&query=cabin+lake"
    },
    {
        "type": "Cabin",
        "region": "Barron & Dunn / Chippewa Valley",
        "url": "https://www.craigslist.org/search/area/eauclaire?cat=rea&query=cabin+lake"
    },
    {
        "type": "Cabin",
        "region": "Washburn County / Spooner Area",
        "url": "https://www.craigslist.org/search/area/duluth?cat=rea&query=cabin+lake"
    }
]

# Keywords to ensure we only catch target Wisconsin areas
TARGET_COUNTIES_KEYWORDS = [
    "polk", "barron", "dunn", "washburn", "spooner", "rice lake", 
    "balsam lake", "cumberland", "menomonie", "shell lake", "birchwood",
    "chetek", "luck", "milltown", "amery", "frederic", "siren", "turtle lake"
]


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def load_seen_ids():
    """Loads previously alerted post IDs from a file."""
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_seen_id(post_id):
    """Appends a new post ID to the seen file."""
    with open(SEEN_FILE, "a") as f:
        f.write(f"{post_id}\n")

def extract_acreage(title):
    """Extracts acreage numbers from the title if mentioned."""
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:acre|acres|ac)\b', title, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def is_valid_match(item, target_config):
    """Filters listings based on type, acreage, and county keywords."""
    title_lower = item['title'].lower()
    
    # Optional County Keyword Verification
    # Matches if any target town/county is mentioned in title or location
    location_lower = item['location'].lower()
    location_match = any(k in title_lower or k in location_lower for k in TARGET_COUNTIES_KEYWORDS)

    # 1. FARMLAND FILTER LOGIC
    if target_config['type'] == "Farmland":
        acreage = extract_acreage(title_lower)
        if acreage is not None:
            # Strictly filter out anything under 10 acres if an acreage number was detected
            if acreage < 10.0:
                return False
        # Return True if it mentions 10+ acres or mentions land in our target regions
        return location_match or "acre" in title_lower

    # 2. CABIN FILTER LOGIC
    elif target_config['type'] == "Cabin":
        cabin_keywords = ["cabin", "lake home", "lakefront", "waterfront", "cottage"]
        has_cabin_kw = any(kw in title_lower for kw in cabin_keywords)
        return has_cabin_kw and location_match

    return False


# ==========================================
# SCRAPER LOGIC
# ==========================================
def scrape_craigslist():
    seen_ids = load_seen_ids()
    new_matches = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    print("Checking Craigslist for Wisconsin Farmland & Cabins...")

    for config in SEARCH_TARGETS:
        try:
            res = requests.get(config["url"], headers=headers, timeout=10)
            if res.status_code != 200:
                print(f"Failed to fetch {config['region']} (Status: {res.status_code})")
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            results = soup.select("li.cl-static-search-result") or soup.select("ol.cl-static-search-results li")

            for result in results:
                # Locate the link tag first
                a_elem = result.select_one("a")
                if not a_elem or not a_elem.get("href"):
                    continue

                link = a_elem.get("href")
                if link.startswith("/"):
                    link = "https://www.craigslist.org" + link

                # Extract title from title div or fallback to anchor text
                title_elem = result.select_one(".title") or a_elem
                title = title_elem.text.strip() if title_elem else "No Title"

                # Extract listing ID from URL safely
                post_id_match = re.search(r'/(\d+)\.html', link)
                if not post_id_match:
                    continue
                post_id = post_id_match.group(1)

                if post_id in seen_ids:
                    continue

                price_elem = result.select_one(".price")
                loc_elem = result.select_one(".location")

                price = price_elem.text.strip() if price_elem else "N/A"
                location = loc_elem.text.strip() if loc_elem else "WI"

                item = {
                    "id": post_id,
                    "title": title,
                    "price": price,
                    "location": location,
                    "link": link,
                    "region": config["region"],
                    "type": config["type"]
                }

                if is_valid_match(item, config):
                    new_matches.append(item)
                    seen_ids.add(post_id)
                    save_seen_id(post_id)

        except Exception as e:
            print(f"Error scraping {config['region']}: {e}")

    return new_matches



# ==========================================
# EMAIL ALERT LOGIC
# ==========================================
def send_email_alert(listings):
    if not listings:
        print("No new matches found.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌲 WI Real Estate Alert: {len(listings)} New Listing(s) Found"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    # Build HTML Email Body
    html_items = ""
    for item in listings:
        badge_color = "#2e7d32" if item['type'] == "Farmland" else "#0277bd"
        html_items += f"""
        <div style="border: 1px solid #ddd; padding: 15px; margin-bottom: 15px; border-radius: 8px;">
            <span style="background-color: {badge_color}; color: white; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">
                {item['type'].upper()}
            </span>
            <span style="color: #666; font-size: 12px; margin-left: 10px;">{item['region']}</span>
            <h3 style="margin: 8px 0 4px 0;"><a href="{item['link']}" style="color: #1a0dab; text-decoration: none;">{item['title']}</a></h3>
            <p style="margin: 0; font-size: 16px; font-weight: bold; color: #2d3748;">
                Price: {item['price']} | Location: {item['location']}
            </p>
        </div>
        """

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #1b5e20;">Northwestern WI Land & Cabin Alert</h2>
        <p>The following new listings were found in Barron, Polk, Dunn, or Washburn counties:</p>
        {html_items}
      </body>
    </html>
    """

    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"Successfully sent email alert for {len(listings)} listing(s)!")
    except Exception as e:
        print(f"Failed to send email: {e}")


# ==========================================
# MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    matches = scrape_craigslist()
    send_email_alert(matches)