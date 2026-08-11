import os
import sys
import time
import re
import smtplib
import imaplib
import email
import xml.etree.ElementTree as ET
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

# Craigslist Regional RSS Feeds for Golf Carts
CRAIGSLIST_FEEDS = [
    {"region": "Eau Claire", "url": "https://eauclaire.craigslist.org/search/sss?format=rss&query=golf+cart"},
    {"region": "Duluth / Superior", "url": "https://duluth.craigslist.org/search/sss?format=rss&query=golf+cart"},
    {"region": "Minneapolis / St. Paul", "url": "https://minneapolis.craigslist.org/search/sss?format=rss&query=golf+cart"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
    subject = f"🛒 GOLF CART DEAL FOUND: {title[:50]}"
    
    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #fef8f0; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 25px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
          <span style="background-color: #e65100; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">{source} ({region})</span>
          <h2 style="color: #bf360c; margin-top: 15px;">Used Golf Cart Listing</h2>
          <p style="font-size: 18px; color: #333333;"><strong>{title}</strong></p>
          <div style="margin-top: 20px;">
            <a href="{url}" style="background-color: #e65100; color: white; padding: 12px 20px; text-decoration: none; border-radius: 5px; display: inline-block; font-weight: bold;">View Golf Cart Listing &rarr;</a>
          </div>
          <hr style="margin-top: 30px; border: None; border-top: 1px solid #eeeeee;">
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
# CRAIGSLIST RSS PARSER
# ==========================================
def check_craigslist_feeds(seen_items):
    print("🔍 Checking Craigslist RSS feeds for Golf Carts...")
    
    for feed_info in CRAIGSLIST_FEEDS:
        region = feed_info["region"]
        url = feed_info["url"]
        print(f"  Fetching RSS for [{region}]...")
        
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            if response.status_code != 200:
                print(f"    Status code {response.status_code} for {region}")
                continue

            root = ET.fromstring(response.content)
            # RSS namespace handling
            items = root.findall(".//{http://purl.org/rss/1.0/}item") or root.findall(".//item")
            
            new_found = 0
            for item in items:
                title_node = item.find("{http://purl.org/rss/1.0/}title") or item.find("title")
                link_node = item.find("{http://purl.org/rss/1.0/}link") or item.find("link")
                
                if title_node is None or link_node is None:
                    continue
                    
                title = title_node.text.strip()
                link = link_node.text.strip()
                
                if link not in seen_items:
                    print(f"\n🚨 NEW CRAIGSLIST GOLF CART FOUND IN {region.upper()}!")
                    send_rich_email_alert(title, link, region, "Craigslist")
                    save_seen_item(link)
                    seen_items.add(link)
                    new_found += 1
            
            if new_found == 0:
                print(f"    No new listings in {region}.")

        except Exception as e:
            print(f"⚠️ Error checking Craigslist feed for {region}: {e}")

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

                    # Check if email contains golf cart terms
                    if "golf cart" in body.lower():
                        links = re.findall(r'https://nextdoor\.com/for_sale_and_free/[^"\s\'>]+', body)
                        for raw_url in links:
                            clean_url = raw_url.split("?")[0]
                            if clean_url not in seen_items:
                                print(f"\n🚨 NEW NEXTDOOR GOLF CART FOUND VIA EMAIL!")
                                send_rich_email_alert("Nextdoor Golf Cart Listing", clean_url, "Local Area", "Nextdoor")
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
    print("🚀 Starting Used Golf Cart Monitor check...")
    
    check_craigslist_feeds(seen_items)
    check_nextdoor_emails(seen_items)

    print("\n✅ Golf cart monitor execution completed successfully.")

if __name__ == "__main__":
    main()
