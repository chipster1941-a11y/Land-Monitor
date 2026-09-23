import os
import json
import time
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Credentials & Config from Environment Variables
GDV_MEMBER_ID = os.getenv("GDV_MEMBER_ID", "")
GDV_PASSWORD = os.getenv("GDV_PASSWORD", "")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", os.getenv("EMAIL_SENDER", ""))
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", os.getenv("EMAIL_PASSWORD", ""))
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL", os.getenv("EMAIL_RECEIVER", ""))

SEEN_FILE = "seen_gdv_weeks.json"

TARGET_MONTHS = [
    "November, 2026",
    "December, 2026",
    "January, 2027",
    "February, 2027",
    "September, 2027"
]

def load_seen_items():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Error loading {SEEN_FILE}: {e}")
    return set()

def save_seen_items(seen_items):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen_items), f, indent=2)
        logging.info(f"Saved {len(seen_items)} seen items to {SEEN_FILE}")
    except Exception as e:
        logging.error(f"Error saving {SEEN_FILE}: {e}")

def send_email_notification(new_matches):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECEIVER_EMAIL:
        logging.warning("Email credentials incomplete. Skipping email dispatch.")
        return

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = f"GDV Monitor Alert: {len(new_matches)} New Resort Listings Found!"

    body = "New GDV Condo Inventory Matches Found:\n\n"
    for item in new_matches:
        body += f"- {item.get('title', 'Unknown Title')}\n"
        body += f"  Month: {item.get('month', 'N/A')}\n"
        body += f"  Details: {item.get('details', 'N/A')}\n"
        body += f"  Link: {item.get('link', 'N/A')}\n\n"

    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        logging.info("Successfully sent email notification.")
    except Exception as e:
        logging.error(f"Failed to send email notification: {e}")

def login_gdv(page):
    logging.info("Navigating to GDV Member Portal...")
    page.goto("https://globaldiscoveryvacations.com/login.aspx")
    page.wait_for_load_state("networkidle")

    # Perform login if redirected to login page
    if "login.aspx" in page.url.lower():
        logging.info("Submitting member login credentials...")
        page.fill("input[id*='Username']", GDV_MEMBER_ID)
        page.fill("input[id*='Password']", GDV_PASSWORD)
        page.click("input[type='submit'], button[id*='Login']")
        page.wait_for_load_state("networkidle")

    logging.info(f"Member login successful! Current URL: {page.url}")

def select_month_and_search(page, target_month_str):
    # Only navigate to main search page if not already on the condo/members portal
    if "members.aspx" not in page.url.lower() and "condo" not in page.url.lower():
        logging.info("Navigating to condo portal...")
        page.goto("https://globaldiscoveryvacations.com/members.aspx")
        page.wait_for_load_state("networkidle")

    logging.info(f"Opening month dropdown for {target_month_str}...")
    
    # Open month dropdown
    dropdown = page.locator("a:has-text('Select Month'), .dropdown-toggle, #ddlMonth").first
    if dropdown.count() > 0 and dropdown.is_visible():
        dropdown.click()
        page.wait_for_timeout(1000)

    # Locate the lbMonth LinkButton for the target month
    month_option = page.locator(f"a[id*='lbMonth']:has-text('{target_month_str}')").first

    if month_option.count() > 0:
        logging.info(f"Clicking lbMonth LinkButton for {target_month_str}...")
        href = month_option.get_attribute("href")
        
        if href and href.startswith("javascript:"):
            # Execute ASP.NET __doPostBack directly in browser context
            page.evaluate(href.replace("javascript:", ""))
        else:
            month_option.click(force=True)

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
    else:
        logging.warning(f"Could not locate month option for '{target_month_str}'")

def extract_resort_cards(page, month_label):
    logging.info(f"Extracting resort cards for {month_label}...")
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    items = []
    # Query for resort cards/containers
    cards = soup.select(".resort-card, .resortItem, .condo-listing")
    
    if not cards:
        # Fallback card locator if container classes differ
        cards = soup.select("div[id*='Resort'], div[class*='resort']")

    for idx, card in enumerate(cards):
        title_el = card.select_one(".resort-name, .title, h3, h4, a[id*='lbResort']")
        title = title_el.get_text(strip=True) if title_el else f"Resort Listing #{idx+1}"
        
        link_el = card.select_one("a[href]")
        link = link_el["href"] if link_el else page.url

        details_el = card.select_one(".details, .location, .description")
        details = details_el.get_text(strip=True) if details_el else "N/A"

        # Unique key identifier for state tracking
        item_id = f"{month_label}_{title}"

        items.append({
            "id": item_id,
            "title": title,
            "month": month_label,
            "details": details,
            "link": link
        })

    logging.info(f"Extracted {len(items)} items for {month_label}.")
    return items

def run_scraper():
    seen_items = load_seen_items()
    new_matches = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        try:
            login_gdv(page)

            for month_label in TARGET_MONTHS:
                try:
                    select_month_and_search(page, month_label)
                    month_items = extract_resort_cards(page, month_label)

                    for item in month_items:
                        if item["id"] not in seen_items:
                            seen_items.add(item["id"])
                            new_matches.append(item)

                except Exception as e:
                    logging.error(f"Error processing month {month_label}: {e}")

        except Exception as e:
            logging.error(f"Global scraping error: {e}")
        finally:
            browser.close()

    logging.info(f"Scan complete. Total queue length for email dispatch: {len(new_matches)}")

    if new_matches:
        save_seen_items(seen_items)
        send_email_notification(new_matches)
    else:
        logging.info("No new listings found on this run.")

if __name__ == "__main__":
    run_scraper()