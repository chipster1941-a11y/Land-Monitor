import os
import sys
import time
import re
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from curl_cffi import requests

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "chipster1941@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "ygyk ijmw qowl qpad")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "kwolf100@hotmail.com")

SEEN_FILE = "seen_golf_carts.txt"

# ------------------------------------------
# FILTERING CONFIGURATION
# ------------------------------------------
# Target brands: Set to ["yamaha"] for Yamaha only, or [] to allow all brands
TARGET_BRANDS = ["yamaha", "club car", "ezgo", "ez-go", "ez go"]

# Negative keywords to automatically discard junk / non-vehicle items
EXCLUDE_KEYWORDS = [
    "golf club", "golf clubs", "club set", "driver", "putter", "iron set", "irons", "wedges",
    "golf bag", "cart bag", "stand bag", "golf ball", "golf balls", "golf shoes", "golf shirt",
    "kayak", "kayaks", "keyboard", "keyboards", "outboard", "push cart", "pull cart", 
    "hand cart", "trolley", "towel", "headcover", "glove", "gloves", "apparel", "hat"
]

# Craigslist Area Search Endpoints (Dozer Format)
CRAIGSLIST_SEARCHES = [
    {
        "region": "Tampa Bay Area", 
        "url": "https://www.craigslist.org/search/area/tampa?cat=sss&query=golf+cart"
    },
    {
        "region": "Sarasota / Bradenton", 
        "url": "https://www.craigslist.org/search/area/sarasota?cat=sss&query=golf+cart"
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ==========================================
# MEMORY & PERSISTENCE
# ==========================================
def load_seen_items():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_item(item_url):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{item_url}\n")

# ==========================================
# FILTERING ENGINE
# ==========================================
def is_valid_golf_cart_listing(text):
    """Evaluates text against brand requirements and negative keywords."""
    text_lower = text.lower()
    
    # 1. Reject if any negative keyword phrase is present
    for bad_phrase in EXCLUDE_KEYWORDS:
        if bad_phrase in text_lower:
            return False
            
    # 2. Require target brand match if specified
    if TARGET_BRANDS:
        has_brand = any(brand.lower() in text_lower for brand in TARGET_BRANDS)
        if not has_brand:
            return False

    return True

# ==========================================
# EMAIL NOTIFICATION FUNCTIONS
# ==========================================
def send_rich_email_alert(title, url, region, source):
    subject = f"🛒 YAMAHA GOLF CART DEAL ({region}): {title[:40]}"
    
    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #fef8f0; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 25px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
          <span style="background-color: #e65100; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">{source} ({region})</span>
          <h2 style="color: #bf360c; margin-top: 15px;">Matched Golf Cart Listing</h2>
          <p style="font-size: 18px; color: #333333;"><strong>{title}</strong></p>
          <div style="margin-top: 20px;">
            <a href="{url}" style="background-color: #e65100; color: white; padding: 12px 20px; text-decoration: none; border-radius: 5px; display: inline-block; font-weight: bold;">View Listing Details &rarr;</a>
          </div>
          <hr style="margin-top: 30px; border: none; border-top: 1px solid #eeeeee;">
          <p style="font-size: 12px; color: #888888;">Automated alert from Golf Cart Finder System.</p>
        </div>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print(f"  ✉️ Email alert sent for: {title}")
    except Exception as e:
        print(f"  ❌ Failed to send email: {e}")

# ==========================================
# CRAIGSLIST AREA SEARCH SCRAPER
# ==========================================
def check_craigslist(seen_items):
    print("🔍 Checking Florida Craigslist listings...")
    
    for search_info in CRAIGSLIST_SEARCHES:
        region = search_info["region"]
        target_url = search_info["url"]
        print(f"  Fetching [{region}]...")
        
        try:
            resp = requests.get(target_url, headers=HEADERS, impersonate="chrome", timeout=15)
            if resp.status_code != 200:
                print(f"    Http status {resp.status_code} received for {region}.")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            listing_links = soup.find_all("a", href=re.compile(r"/d/|\.html", re.I))
            new_found = 0
            
            for a_tag in listing_links:
                href = a_tag.get("href", "")
                if not href or "search" in href:
                    continue
                
                clean_url = href.split("?")[0]
                
                title = a_tag.text.strip()
                if not title or len(title) < 3:
                    parent = a_tag.find_parent(["li", "div", "h3"])
                    if parent:
                        title = parent.text.strip()
                
                title = " ".join((title or f"Golf Cart Listing in {region}").split())
                
                # Check uniqueness AND brand/negative filters
                if clean_url not in seen_items and len(title) > 3:
                    if is_valid_golf_cart_listing(title):
                        print(f"\n🚨 NEW YAMAHA GOLF CART FOUND IN {region.upper()}!")
                        send_rich_email_alert(title, clean_url, region, "Craigslist")
                        save_seen_item(clean_url)
                        seen_items.add(clean_url)
                        new_found += 1
                    else:
                        # Silently skip items that don't match our criteria (clubs, kayaks, other brands)
                        pass

            if new_found == 0:
                print(f"    No new matching listings found in {region}.")

        except Exception as e:
            print(f"    Error querying {region}: {e}")

        time.sleep(2)

# ==========================================
# NEXTDOOR EMAIL IMAP PARSER
# ==========================================
def check_nextdoor_emails(seen_items):
    print("\n🔍 Checking Gmail inbox for Nextdoor alert emails...")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(SENDER_EMAIL, SENDER_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN FROM "nextdoor.com")')
        if status != "OK" or not messages[0]:
            print("  No new unread Nextdoor alert emails found.")
            mail.logout()
            return

        email_ids = messages[0].split()
        new_found_count = 0

        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/html":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                    # Extract Nextdoor links if the email mentions a valid matching cart
                    if is_valid_golf_cart_listing(body):
                        links = re.findall(r'https://nextdoor\.com/for_sale_and_free/[^"\s\'>]+', body)
                        for raw_url in links:
                            clean_url = raw_url.split("?")[0]
                            if clean_url not in seen_items:
                                print(f"\n🚨 NEW NEXTDOOR YAMAHA GOLF CART FOUND VIA EMAIL!")
                                send_rich_email_alert("Nextdoor Yamaha Golf Cart Listing", clean_url, "Florida Area", "Nextdoor")
                                save_seen_item(clean_url)
                                seen_items.add(clean_url)
                                new_found_count += 1

            mail.store(e_id, '+FLAGS', '\\Seen')

        mail.logout()
        if new_found_count == 0:
            print("  Nextdoor emails processed, but no new unique Yamaha golf cart links were found.")

    except Exception as e:
        print(f"⚠️ Error reading Nextdoor emails via IMAP: {e}")

# ==========================================
# MAIN ENTRY POINT
# ==========================================
def main():
    seen_items = load_seen_items()
    print("🚀 Starting Florida Golf Cart Monitor check...")
    
    check_craigslist(seen_items)
    check_nextdoor_emails(seen_items)

    print("\n✅ Golf cart monitor execution completed successfully.")

if __name__ == "__main__":
    main()