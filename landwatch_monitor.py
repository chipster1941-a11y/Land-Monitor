import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from curl_cffi import requests

# ==========================================
# --- CONFIGURATION ---
# ==========================================

SENDER_EMAIL = "chipster1941@gmail.com"
SENDER_PASSWORD = "ygyk ijmw qowl qpad"  # Paste your 16-character App Password here
RECIPIENT_EMAIL = "kwolf100@hotmail.com"

COUNTIES = [
    "barron-county",
    "polk-county",
    "washburn-county",
    "dunn-county",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

MEMORY_FILE = "seen_farms.txt"


# ==========================================
# --- EMAIL FUNCTION ---
# ==========================================

def send_email_alert(title, link, county):
    county_name = county.replace('-', ' ').title()
    subject = f"🚨 New Farmland Listing in {county_name}!"
    
    body = f"""
    A new farmland listing was found on LandWatch!

    County: {county_name}
    Title:  {title}
    Link:   {link}
    """

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        print(f"  📧 Email notification sent for: {title}")
    except Exception as e:
        print(f"  ⚠️ Failed to send email: {e}")


# ==========================================
# --- SCRAPER LOGIC ---
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


def check_landwatch_for_county(county_slug, seen_properties):
    url = f"https://www.landwatch.com/wisconsin-land-for-sale/{county_slug}/farms-ranches"
    print(f"🔍 Searching LandWatch in [{county_slug.replace('-', ' ').title()}]...")

    try:
        response = requests.get(
            url, 
            headers=HEADERS, 
            impersonate="chrome", 
            timeout=15
        )
        if response.status_code != 200:
            print(f"⚠️ Failed to reach {county_slug}. HTTP Status: {response.status_code}")
            return
    except Exception as e:
        print(f"⚠️ Connection error for {county_slug}: {e}")
        return

    soup = BeautifulSoup(response.text, "html.parser")
    all_links = soup.find_all("a", href=True)
    
    new_found_count = 0

    for a_tag in all_links:
        href = a_tag["href"]

        # Ensure link is a property AND contains 'wisconsin'
        if ("/land-for-sale/pid/" in href or "/farms-ranches/" in href) and "wisconsin" in href.lower():
            full_url = href if href.startswith("http") else f"https://www.landwatch.com{href}"
            
            title = a_tag.text.strip() or "Farmland Listing"
            title = " ".join(title.split())

            if full_url not in seen_properties:
                print(f"\n🚨 NEW LISTING IN {county_slug.upper()}!")
                print(f"Title: {title}")
                print(f"Link:  {full_url}")

                send_email_alert(title, full_url, county_slug)

                save_seen_property(full_url)
                seen_properties.add(full_url)
                new_found_count += 1

    if new_found_count == 0:
        print(f"  No new listings in {county_slug.replace('-', ' ').title()}.")


def main():
    
    seen_properties = load_seen_properties()
    for county in COUNTIES:
        check_landwatch_for_county(county, seen_properties)
        time.sleep(2)
    print("\nCheck complete!")


if __name__ == "__main__":
    main()