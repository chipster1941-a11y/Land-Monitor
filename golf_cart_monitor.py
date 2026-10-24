import os
import re
import json
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

# Safely load local .env if python-dotenv is available (for local testing)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Logging Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# File Paths & Configuration
SEEN_FILE = "seen_golf_carts.json"
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "")

# Broad Matching Keywords: Yamaha, E-Z-GO, Club Car, and general gas/golf cart terms
TARGET_KEYWORDS = [
    "yamaha", "ezgo", "e-z-go", "club car", "drive2", "quietech", 
    "txt", "rxv", "precedent", "onward", "golf cart", "gas cart", "golf car"
]

# Exclusion Terms to filter out parts/accessories listings
PARTS_EXCLUSIONS = ["wheel", "tire", "charger", "cover", "enclosure", "windshield", "battery", "lift kit"]


def load_seen_items():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"Could not load {SEEN_FILE}: {e}")
    return []


def save_seen_items(seen_list):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(seen_list, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save {SEEN_FILE}: {e}")


def is_relevant_listing(title):
    title_lower = title.lower()
    has_target = any(kw in title_lower for kw in TARGET_KEYWORDS)
    is_part = any(p in title_lower for p in PARTS_EXCLUSIONS)
    return has_target and not is_part


def scrape_craigslist(page, location_name, search_url):
    logging.info(f"Checking {location_name} Craigslist...")
    matched_items = []
    try:
        page.goto(search_url, wait_until="networkidle", timeout=30000)
        
        # Multiple fallback selectors for Craigslist layout iterations
        cards = page.query_selector_all(".result-node, .cl-static-search-result, li.cl-search-result, .gallery-card")
        logging.info(f"Found {len(cards)} raw {location_name} Craigslist result items.")
        
        for card in cards:
            text = card.inner_text().strip()
            
            # Extract price if present
            price_elem = card.query_selector(".price, .priceinfo")
            price_text = price_elem.inner_text().strip() if price_elem else ""
            
            # Extract title and URL anchor
            link_elem = card.query_selector("a.title, a.cl-app-anchor, a")
            url = link_elem.get_attribute("href") if link_elem else search_url
            
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            title = lines[0] if lines else text
            
            if is_relevant_listing(title):
                full_title = f"{title} ({price_text})" if price_text else title
                matched_items.append({
                    "title": full_title,
                    "url": url,
                    "source": f"Craigslist ({location_name})"
                })
    except Exception as e:
        logging.error(f"Error scraping {location_name} Craigslist: {e}")
    return matched_items


def scrape_facebook(page, location_name, search_url):
    logging.info(f"Checking {location_name} Facebook Marketplace...")
    matched_items = []
    try:
        page.goto(search_url, wait_until="networkidle", timeout=30000)
        
        # Save visual debug snapshot
        debug_filename = f"fb_debug_{location_name.lower()}.png"
        page.screenshot(path=debug_filename)
        logging.info(f"Saved {debug_filename} for Facebook debugging.")
        
        # Primary container card selectors for Marketplace feed
        cards = page.query_selector_all("div[role='main'] div[style*='max-width'], div[role='feed'] > div, div.x9f619")
        logging.info(f"Found {len(cards)} Facebook Marketplace cards for {location_name}.")
        
        for card in cards:
            text = card.inner_text().strip()
            if not text:
                continue
                
            # Extract link element if available within card
            link_elem = card.query_selector("a[href*='/marketplace/item/']")
            href = link_elem.get_attribute("href") if link_elem else None
            full_url = f"https://www.facebook.com{href}" if href and href.startswith("/") else search_url
            
            lines = [l for l in text.splitlines() if l.strip()]
            title_candidate = lines[0] if lines else text
            
            if is_relevant_listing(text):
                matched_items.append({
                    "title": title_candidate,
                    "url": full_url,
                    "source": f"Facebook ({location_name})"
                })
    except Exception as e:
        logging.error(f"Error scraping {location_name} Facebook: {e}")
    return matched_items


def scrape_nextdoor(page, search_url):
    logging.info("Checking Nextdoor For Sale & Free...")
    matched_items = []
    try:
        page.goto(search_url, wait_until="networkidle", timeout=30000)
        page.screenshot(path="nextdoor_debug.png")
        logging.info("Saved nextdoor_debug.png for Nextdoor debugging.")
        
        elements = page.query_selector_all("a[href*='/for_sale_and_free/'], div[data-testid='fsf-item-card']")
        logging.info(f"Found {len(elements)} raw Nextdoor elements.")
        
        for elem in elements:
            text = elem.inner_text().strip()
            href = elem.get_attribute("href")
            full_url = f"https://nextdoor.com{href}" if href and href.startswith("/") else search_url
            
            if text and is_relevant_listing(text):
                lines = [l for l in text.splitlines() if l.strip()]
                title_candidate = lines[0] if lines else text
                matched_items.append({
                    "title": title_candidate,
                    "url": full_url,
                    "source": "Nextdoor"
                })
    except Exception as e:
        logging.error(f"Error scraping Nextdoor: {e}")
    return matched_items


def send_email_alert(new_items):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAIL:
        logging.warning("Email credentials missing in environment. Skipping email dispatch.")
        return

    subject = f"Golf Cart Alert: {len(new_items)} New Listing(s) Found!"
    
    body = "New Golf Cart Listings Found:\n\n"
    for item in new_items:
        body += f"• {item['title']}\n  Source: {item['source']}\n  Link: {item['url']}\n\n"

    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        server.quit()
        logging.info(f"Email alert successfully sent to {RECIPIENT_EMAIL}.")
    except Exception as e:
        logging.error(f"Failed to send email alert: {e}")


def main():
    logging.info("Starting Playwright Scraper for FB, Craigslist, and Nextdoor...")
    seen_items = load_seen_items()
    notification_queue = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # 1. Scrape Craigslist
        cl_tampa = scrape_craigslist(page, "Tampa", "https://tampa.craigslist.org/search/sss?query=golf+cart")
        cl_sarasota = scrape_craigslist(page, "Sarasota", "https://sarasota.craigslist.org/search/sss?query=golf+cart")
        cl_matches = cl_tampa + cl_sarasota
        logging.info(f"Craigslist section added {len(cl_matches)} total matching items.")
        notification_queue.extend(cl_matches)

        # 2. Scrape Facebook Marketplace
        fb_tampa = scrape_facebook(page, "Tampa", "https://www.facebook.com/marketplace/tampa/search?query=golf%20cart")
        fb_sarasota = scrape_facebook(page, "Sarasota", "https://www.facebook.com/marketplace/sarasota/search?query=golf%20cart")
        fb_matches = fb_tampa + fb_sarasota
        logging.info(f"Facebook section added {len(fb_matches)} total matching items.")
        notification_queue.extend(fb_matches)

        # 3. Scrape Nextdoor
        nd_matches = scrape_nextdoor(page, "https://nextdoor.com/for_sale_and_free/?query=golf%20cart")
        logging.info(f"Nextdoor section added {len(nd_matches)} items to notification queue.")
        notification_queue.extend(nd_matches)

        browser.close()

    # Filter out previously alerted listings
    new_alerts = []
    for item in notification_queue:
        item_id = item["title"] + item["url"]
        if item_id not in seen_items:
            new_alerts.append(item)
            seen_items.append(item_id)

    logging.info(f"Scan complete. Total new items queued for email dispatch: {len(new_alerts)}")

    if new_alerts:
        send_email_alert(new_alerts)
        save_seen_items(seen_items)
    else:
        logging.info("No new listings found on this run.")


if __name__ == "__main__":
    main()