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
from bs4 import BeautifulSoup
from curl_cffi import requests

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "your_email@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "your_app_password")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "recipient_email@gmail.com")

TARGET_COUNTIES = ["barron-county", "polk-county", "washburn-county", "dunn-county"]
SEEN_FILE = "seen_cabins.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ==========================================
# MEMORY & PERSISTENCE
# ==========================================
def load_seen_properties():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_property(property_url):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{property_url}\n")

# ==========================================
# EMAIL NOTIFICATION FUNCTIONS
# ==========================================
def send_rich_email_alert(title, url, county, source):
    county_formatted = county.replace('-', ' ').title()
    subject = f"🌊 NEW LAKE CABIN / WATERFRONT LISTING: {county_formatted}"
    
    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #f0f4f8; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 25px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
          <span style="background-color: #0288d1; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">{source} (Waterfront)</span>
          <h2 style="color: #01579b; margin-top: 15px;">New Cabin / Lake Property - {county_formatted}</h2>
          <p style="font-size: 16px; color: #333333;"><strong>{title}</strong></p>
          <div style="margin-top: 20px;">
            <a href="{url}" style="background-color: #0288d1; color: white; padding: 12px 20px; text-decoration: none; border-radius: 5px; display: inline-block; font-weight: bold;">View Listing Details &rarr;</a>
          </div>
          <hr style="margin-top: 30px; border: None; border-top: 1px solid #eeeeee;">
          <p style="font-size: 12px; color: #888888;">Automated alert from Lake Cabin Monitor System (Barron, Polk, Washburn, Dunn).</p>
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
# ZILLOW CABIN EMAIL IMAP READER
# ==========================================
def check_zillow_emails(seen_properties):
    print("🔍 Checking Gmail inbox for Zillow Cabin alert emails...")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(SENDER_EMAIL, SENDER_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN FROM "zillow.com")')
        if status != "OK" or not messages[0]:
            print("  No new unread Zillow alert emails found.")
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

                    zillow_links = re.findall(r'https://www\.zillow\.com/homedetails/[^"\s\'>]+', body)
                    
                    for raw_url in zillow_links:
                        clean_url = raw_url.split("?")[0]
                        
                        if clean_url not in seen_properties:
                            print(f"\n🚨 NEW ZILLOW CABIN LISTING FOUND VIA EMAIL!")
                            title = "Zillow Waterfront / Cabin Property"
                            send_rich_email_alert(title, clean_url, "Monitored Lake Region", "Zillow")
                            save_seen_property(clean_url)
                            seen_properties.add(clean_url)
                            new_found_count += 1

            mail.store(e_id, '+FLAGS', '\\Seen')

        mail.logout()
        if new_found_count == 0:
            print("  Zillow emails processed, but no new unique cabin links were found.")

    except Exception as e:
        print(f"⚠️ Error reading Zillow emails via IMAP: {e}")

# ==========================================
# LANDWATCH WATERFRONT SCRAPER
# ==========================================
def check_landwatch_waterfront(county_slug, seen_properties):
    county_name = county_slug.replace('-', ' ').title()
    url = f"https://www.landwatch.com/wisconsin-land-for-sale/{county_slug}/waterfront"
    print(f"🔍 Searching LandWatch Waterfront in [{county_name}]...")

    try:
        response = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
        if response.status_code != 200:
            print(f"  LandWatch status: {response.status_code}")
            return
    except Exception as e:
        print(f"⚠️ Connection error for LandWatch ({county_slug}): {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    property_links = soup.find_all("a", href=re.compile(r"/pid/", re.I))
    new_found_count = 0

    for a_tag in property_links:
        href = a_tag["href"]
        full_url = href if href.startswith("http") else f"https://www.landwatch.com{href}"
        title = a_tag.text.strip() or f"Waterfront Listing in {county_name}"
        title = " ".join(title.split())

        if full_url not in seen_properties:
            print(f"\n🚨 NEW LANDWATCH WATERFRONT LISTING IN {county_slug.upper()}!")
            send_rich_email_alert(title, full_url, county_slug, "LandWatch")
            save_seen_property(full_url)
            seen_properties.add(full_url)
            new_found_count += 1

    if new_found_count == 0:
        print(f"  No new LandWatch waterfront listings in {county_name}.")

# ==========================================
# MAIN ENTRY POINT
# ==========================================
def main():
    seen_properties = load_seen_properties()
    print("🚀 Starting Lake Cabin / Waterfront Monitor check...")
    
    for county in TARGET_COUNTIES:
        check_landwatch_waterfront(county, seen_properties)

    check_zillow_emails(seen_properties)

    print("\n✅ Cabin monitor execution completed successfully.")

if __name__ == "__main__":
    main()
