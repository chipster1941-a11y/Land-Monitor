import os
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

MEMORY_FILE = "seen_farms.txt"


# ==========================================
# --- EMAIL FUNCTION ---
# ==========================================

def send_email_alert(title, link, county, source_site):
    county_name = county.replace('-', ' ').title()
    subject = f"🚨 New Farmland Listing on {source_site} ({county_name})!"
    
    body = f"""
    A new farmland listing was found on {source_site}!

    Source: {source_site}
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
    county_name = county_slug.replace('-', ' ').title()
    county_raw = county_slug.split('-')[0]  # e.g., 'barron', 'polk'
    url = f"https://www.landwatch.com/wisconsin-land-for-sale/{county_slug}/farms-ranches"
    print(f"🔍 Searching LandWatch in [{county_name}]...")

    try:
        response = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
        if response.status_code != 200:
            print(f"⚠️ Failed to reach LandWatch for {county_slug}. HTTP Status: {response.status_code}")
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
        
        # Verify the listing URL or title actually belongs to the target county
        title = a_tag.text.strip() or f"Farmland Listing in {county_name}"
        title = " ".join(title.split())
        is_correct_county = county_raw in href.lower() or county_raw in title.lower()

        if is_property_link and is_wisconsin and is_not_search_page and is_correct_county:
            full_url = href if href.startswith("http") else f"https://www.landwatch.com{href}"

            if full_url not in seen_properties:
                print(f"\n🚨 NEW LANDWATCH LISTING IN {county_slug.upper()}!")
                print(f"Title: {title}")
                print(f"Link:  {full_url}")

                send_email_alert(title, full_url, county_slug, "LandWatch")
                save_seen_property(full_url)
                seen_properties.add(full_url)
                new_found_count += 1

    if new_found_count == 0:
        print(f"  No new LandWatch listings in {county_name}.")


def check_landandfarm_for_county(county_slug, seen_properties):
    county_name = county_slug.replace('-', ' ').title()
    county_raw = county_slug.split('-')[0]  # e.g., 'barron', 'polk'
    url = f"https://www.landandfarm.com/wisconsin-land-for-sale/{county_slug}/farms-ranches/"
    print(f"🔍 Searching LandAndFarm in [{county_name}]...")

    try:
        response = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
        if response.status_code != 200:
            print(f"⚠️ Failed to reach LandAndFarm for {county_slug}. HTTP Status: {response.status_code}")
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

        # Verify the listing URL or title actually belongs to the target county
        title = a_tag.text.strip() or f"LandAndFarm Listing in {county_name}"
        title = " ".join(title.split())
        is_correct_county = county_raw in href.lower() or county_raw in title.lower()

        if is_property_link and is_wisconsin and is_correct_county:
            full_url = href if href.startswith("http") else f"https://www.landandfarm.com{href}"

            if full_url not in seen_properties:
                print(f"\n🚨 NEW LANDANDFARM LISTING IN {county_slug.upper()}!")
                print(f"Title: {title}")
                print(f"Link:  {full_url}")

                send_email_alert(title, full_url, county_slug, "LandAndFarm")
                save_seen_property(full_url)
                seen_properties.add(full_url)
                new_found_count += 1

    if new_found_count == 0:
        print(f"  No new LandAndFarm listings in {county_name}.")


def main():
    seen_properties = load_seen_properties()
    
    for county in COUNTIES:
        check_landwatch_for_county(county, seen_properties)
        time.sleep(2)
        check_landandfarm_for_county(county, seen_properties)
        time.sleep(2)
        
    print("\nCheck complete!")


if __name__ == "__main__":
    main()