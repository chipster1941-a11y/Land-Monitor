import os
import sys
import time
import re
import smtplib
import imaplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from curl_cffi import requests

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "your_email@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "your_app_password")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "recipient_email@gmail.com")

SEEN_FILE = "seen_golf_carts.txt"

# Craigslist Regional Feeds converted via RSS2JSON API (bypasses 503 blocks)
CRAIGSLIST_FEEDS = [
    {
        "region": "Tampa Bay Area", 
        "rss_url": "https://tampa.craigslist.org/search/sss?format=rss&query=golf+cart"
    },
    {
        "region": "Sarasota / Bradenton", 
        "rss_url": "https://sarasota.craigslist.org/search/sss?format=rss&query=golf+cart"
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
# EMAIL NOTIFICATION FUNCTIONS
# ==========================================
def send_rich_email_alert(title, url, region, source):
    subject = f"🛒 GOLF CART DEAL FOUND ({region}): {title[:40]}"
    
    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #fef8f0; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 25px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
          <span style="background-color: #e65100; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">{source} ({region})</span>
          <h2 style="color: #bf360c; margin-top: 15px;">Florida Golf Cart Listing</h2>
          <p style="font-size: 18px; color: #333333;"><strong>{title}</strong></p>
          <div style="margin-top: 20px;">
            <a href="{url}" style="background-color: #e65100; color: white; padding: 12px 20px; text-decoration: none; border-radius: 5px; display: inline-block; font-weight: bold;">View Listing Details &rarr;</a>
          </div>
          <hr style="margin-top: 30px; border: None; border-top: 1px solid #eeeeee;">
          <p style="font-size: 12px; color: #888888;">Automated alert from Golf Cart Finder System (Florida Region).</p>
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
# CRAIGSLIST VIA RSS2JSON PROXY PARSER
# ==========================================
def check_craigslist_rss(seen_items):
    print("🔍 Checking Florida Craigslist feeds via RSS2JSON proxy...")
    
    for feed_info in CRAIGSLIST_FEEDS:
        region = feed_info["region"]
        rss_url = feed_info["rss_url"]
        
        # Construct proxy request to convert RSS XML to JSON
        api_url = f"https://api.rss2json.com/v1/api.json?rss_url={requests.utils.quote(rss_url)}"
        print(f"  Fetching feed for [{region}]...")
        
        try:
            response = requests.get(api_url, headers=HEADERS, timeout=15)
            if response.status_code != 200:
                print(f"    Status code {response.status_code} for {region}")
                continue

            data = response.json()
            if data.get("status") != "ok":
                print(f"    RSS2JSON error for {region}: {data.get('message', 'Unknown error')}")
                continue

            items = data.get("items", [])
            new_found = 0
            
            for item in items:
                title = item.get("title", "").strip()
                link = item.get("link", "").strip()
                
                if not link or not title:
                    continue
                    
                clean_url = link.split("?")[0]
                
                if clean_url not in seen_items:
                    print(f"\n🚨 NEW CRAIGSLIST GOLF CART FOUND IN {region.upper()}!")
                    send_rich_email_alert(title, clean_url, region, "Craigslist")
                    save_seen_item(clean_url)
                    seen_items.add(clean_url)
                    new_found += 1
            
            if new_found == 0:
                print(f"    No new listings found in {region}.")

            # Polite delay between regional calls
            time.sleep(2)

        except Exception as e:
            print(f"⚠️ Error checking Craigslist for {region}: {e}")

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

                    if "golf cart" in body.lower():
                        links = re.findall(r'https://nextdoor\.com/for_sale_and_free/[^"\s\'>]+', body)
                        for raw_url in links:
                            clean_url = raw_url.split("?")[0]
                            if clean_url not in seen_items:
                                print(f"\n🚨 NEW NEXTDOOR GOLF CART FOUND VIA EMAIL!")
                                send_rich_email_alert("Nextdoor Golf Cart Listing", clean_url, "Florida Area", "Nextdoor")
                                save_seen_item(clean_url)
                                seen_items.add(clean_url)
                                new_found_count += 1

            mail.store(e_id, '+FLAGS', '\\Seen')

        mail.logout()
        if new_found_count == 0:
            print("  Nextdoor emails processed, but no new unique golf cart links were found.")

    except Exception as e:
        print(f"⚠️ Error reading Nextdoor emails via IMAP: {e}")

# ==========================================
# MAIN ENTRY POINT
# ==========================================
def main():
    seen_items = load_seen_items()
    print("🚀 Starting Florida Golf Cart Monitor check...")
    
    check_craigslist_rss(seen_items)
    check_nextdoor_emails(seen_items)

    print("\n✅ Golf cart monitor execution completed successfully.")

if __name__ == "__main__":
    main()
