import os
import re
import csv
import smtplib
import urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ==========================================
# CONFIGURATION
# ==========================================
# Update with your target location and search query
# Example search URL filtered for "golf cart" near Eau Claire / Twin Cities
FB_MARKETPLACE_URL = "https://www.facebook.com/marketplace/tampa/search?query=golf%20cart&exact=false&radius=60"

MAX_GOLF_CART_PRICE = 5000  # Set your budget cap (e.g., $5,000)
USER_DATA_DIR = "./fb_user_data"  # Folder where persistent login cookies are stored

SEEN_FILE = "seen_golf_carts.txt"
CSV_FILE = "matched_golf_carts.csv"

# Email environment variables
EMAIL_SENDER = os.environ.get("SENDER_EMAIL") or os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("SENDER_PASSWORD") or os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("RECIPIENT_EMAIL") or os.environ.get("EMAIL_RECEIVER")


# ==========================================
# HELPERS & TRACKING
# ==========================================
def parse_price(price_str):
    if not price_str or "FREE" in price_str.upper():
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', price_str.split()[0] if price_str.split() else price_str)
    try:
        return float(cleaned)
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
                "source": item.get("source", "Facebook Marketplace"),
                "price": item.get("price", ""),
                "title": item.get("title", ""),
                "location": item.get("location", ""),
                "link": item.get("link", ""),
                "id": item.get("id", "")
            })
    print(f"Logged {len(matches)} golf cart listing(s) to {filename}")


# ==========================================
# FACEBOOK MARKETPLACE SCRAPER
# ==========================================
def scrape_facebook_marketplace(headless=True):
    seen_ids = load_seen_ids()
    matches = []

    print(f"Launching Playwright (Headless: {headless})...")

    with sync_playwright() as p:
        # Launch persistent context to preserve logged-in session
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=headless,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        page = context.pages[0] if context.pages else context.new_page()
        print(f"Navigating to Facebook Marketplace...")
        page.goto(FB_MARKETPLACE_URL, wait_until="domcontentloaded", timeout=45000)

        # If running in setup mode (headed), pause to allow manual log in
        if not headless:
            print("\n" + "="*60)
            print("MANUAL LOGIN MODE:")
            print("1. Log into your Facebook account in the opened browser window.")
            print("2. Set your desired location/radius on Marketplace if needed.")
            print("3. Press ENTER in this terminal once logged in to continue.")
            print("="*60 + "\n")
            input("Press ENTER after completing login...")

        # Wait specifically for Marketplace item links to load into the DOM
        try:
            print("Waiting for Marketplace listing cards to load...")
            page.wait_for_selector('a[href*="/marketplace/item/"]', timeout=15000)
        except Exception as e:
            print(f"Warning: Timed out waiting for listing cards: {e}")

        # Scroll down to trigger lazy-loading of thumbnail images & extra cards
        for _ in range(3):
            page.mouse.wheel(0, 1000)
            page.wait_for_timeout(1500)

        soup = BeautifulSoup(page.content(), "html.parser")
        
        # Marketplace listings are rendered as anchor tags linking to /item/
        listing_anchors = soup.select('a[href*="/marketplace/item/"]')
        print(f"Found {len(listing_anchors)} raw listing cards on page.")

        for a in listing_anchors:
            href = a.get("href", "")
            # Extract unique Facebook item ID
            id_match = re.search(r"/item/(\d+)", href)
            if not id_match:
                continue

            item_id = id_match.group(1)
            full_id = f"fb_cart_{item_id}"

            if full_id in seen_ids:
                continue

            link = f"https://www.facebook.com/marketplace/item/{item_id}/" if not href.startswith("http") else href

            # Extract card text blocks
            card_text = a.get_text(separator="\n").split("\n")
            card_text = [t.strip() for t in card_text if t.strip()]

            if not card_text:
                continue

            # Parse price and title from card text lines
            price_str = "N/A"
            title = "Golf Cart Listing"
            location = "Facebook Marketplace"

            for line in card_text:
                if line.startswith("$") or "FREE" in line.upper():
                    price_str = line
                    break

            # Filter out non-price header strings for title
            filtered_lines = [l for l in card_text if l != price_str and not l.startswith("$")]
            if filtered_lines:
                title = filtered_lines[0]
            if len(filtered_lines) > 1:
                location = filtered_lines[1]

            # Price Budget Filter
            num_price = parse_price(price_str)
            if num_price is not None and num_price > MAX_GOLF_CART_PRICE:
                continue

            # Extract thumbnail photo
            img_elem = a.find("img")
            image_url = img_elem.get("src", "") if img_elem else ""

            # Maps URL lookup for location
            search_query = urllib.parse.quote(f"{title}, {location}")
            map_url = f"https://www.google.com/maps/search/?api=1&query={search_query}"

            item = {
                "id": full_id,
                "title": title,
                "price": price_str,
                "location": location,
                "link": link,
                "source": "Facebook Marketplace",
                "image_url": image_url,
                "map_url": map_url
            }

            matches.append(item)
            seen_ids.add(full_id)
            save_seen_id(full_id)

        context.close()

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

    subject = f"🛒 Golf Cart Alert: {first_match['price']} | {first_match['title']}"
    if count > 1:
        subject = f"🛒 {count} New Golf Cart Listings Found!"

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f1f5f9; padding: 20px 10px; color: #333;">
        <div style="max-width: 600px; margin: auto; background-color: #ffffff; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.08);">
            <h2 style="background-color: #2563eb; color: white; padding: 14px; border-radius: 6px; text-align: center; margin-top: 0;">
                🛒 Facebook Golf Cart Alert ({count})
            </h2>
            <p style="color: #475569; font-size: 14px;">New golf cart listings found on Facebook Marketplace under ${MAX_GOLF_CART_PRICE:,}:</p>
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin-bottom: 20px;">
    """

    for item in matches:
        img_src = item.get("image_url") or "https://via.placeholder.com/150?text=No+Photo"

        html_content += f"""
        <div style="border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 20px; background-color: #f8fafc;">
            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                    <td width="130" valign="top" style="padding-right: 14px;">
                        <img src="{img_src}" alt="Golf Cart" width="120" height="90" style="border-radius: 6px; object-fit: cover; display: block; border: 1px solid #cbd5e1;">
                    </td>
                    <td valign="top">
                        <span style="background-color: #2563eb; color: white; padding: 2px 8px; font-size: 11px; border-radius: 4px; font-weight: bold; text-transform: uppercase;">
                            {item['source']}
                        </span>
                        <h3 style="margin: 6px 0 4px 0; color: #0f172a; font-size: 16px; line-height: 1.2;">{item['title']}</h3>
                        <p style="margin: 2px 0; font-size: 14px;"><strong>Price:</strong> <span style="color: #16a34a; font-weight: bold;">{item['price']}</span></p>
                        <p style="margin: 2px 0; font-size: 13px; color: #64748b;"><strong>Location:</strong> {item['location']}</p>
                    </td>
                </tr>
            </table>

            <div style="margin-top: 14px; padding-top: 12px; border-top: 1px solid #e2e8f0; display: flex; gap: 8px;">
                <a href="{item['link']}" target="_blank" style="background-color: #2563eb; color: white; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 13px; font-weight: bold; inline-block;">
                    View on FB &rarr;
                </a>
                <a href="{item['map_url']}" target="_blank" style="background-color: #475569; color: white; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 13px; inline-block;">
                    📍 Google Maps
                </a>
            </div>
        </div>
        """

    html_content += """
            <p style="font-size: 12px; color: #94a3b8; text-align: center; margin-top: 24px;">
                Facebook Marketplace Golf Cart Monitor
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
        print(f"Email alert sent successfully for {count} golf cart match(es)!")
    except Exception as e:
        print(f"Failed to send email alert: {e}")


# ==========================================
# MAIN RUNNER
# ==========================================
if __name__ == "__main__":
    # Check if persistent context directory exists to determine mode
    has_session = os.path.exists(USER_DATA_DIR) and len(os.listdir(USER_DATA_DIR)) > 0

    if not has_session:
        print("No saved Facebook session found.")
        print("Running ONE-TIME setup mode with visible browser...")
        matches = scrape_facebook_marketplace(headless=False)
    else:
        print("Saved session found. Running automated headless scrape...")
        matches = scrape_facebook_marketplace(headless=True)

    print(f"\nScan complete. Total new golf cart matches found: {len(matches)}")

    if matches:
        save_to_csv(matches, CSV_FILE)
        send_email_alert(matches)
    else:
        print("No new golf cart listings found on this run.")