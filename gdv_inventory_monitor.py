import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Environment Variables
GDV_MEMBER_ID = os.environ.get("GDV_MEMBER_ID")
GDV_PASSWORD = os.environ.get("GDV_PASSWORD")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")

STATE_FILE = "seen_gdv_weeks.json"
TARGET_MONTH_LABEL = "September, 2027"

def load_seen_weeks():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Error reading state file {STATE_FILE}: {e}")
    return set()

def save_seen_weeks(seen_set):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(sorted(list(seen_set)), f, indent=2)
    except Exception as e:
        logging.error(f"Error saving state file {STATE_FILE}: {e}")

def send_email_notification(new_listings):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        logging.warning("Email environment variables missing. Skipping email notification.")
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = f"GDV Alert: {len(new_listings)} New Weeks Available for Fall 2027!"

    body_text = f"Found {len(new_listings)} new matching GDV week(s) for {TARGET_MONTH_LABEL}:\n\n"
    for item in new_listings:
        body_text += f"• {item.get('title', 'N/A')}\n"
        body_text += f"  Dates: {item.get('dates', 'N/A')}\n"
        body_text += f"  Resort: {item.get('resort', 'N/A')}\n"
        body_text += f"  Unit: {item.get('unit', 'N/A')}\n\n"

    msg.attach(MIMEText(body_text, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        logging.info("Email alert sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send email alert: {e}")

def run_gdv_scrape():
    if not GDV_MEMBER_ID or not GDV_PASSWORD:
        logging.error("GDV credentials are missing from environment secrets.")
        return

    seen_weeks = load_seen_weeks()
    new_weeks = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        logging.info("Navigating to GDV Login Portal...")
        page.goto("https://globaldiscoveryvacations.com/agent/login.aspx", wait_until="domcontentloaded", timeout=60000)

        # 1. Authenticate using exact GDV login selectors
        username_selector = "#ctl00_body_tbLoginAgentID"
        password_selector = "input[type='password']"
        submit_selector = "input[type='submit'], button[type='submit']"

        logging.info("Waiting for login inputs...")
        page.wait_for_selector(username_selector, timeout=15000)
        page.fill(username_selector, GDV_MEMBER_ID)
        page.fill(password_selector, GDV_PASSWORD)

        logging.info("Submitting login form...")
        
        # Click login button and explicitly wait for navigation/postback completion
        try:
            with page.expect_navigation(timeout=15000):
                page.click(submit_selector)
        except Exception:
            # Fallback pause if navigation happens via AJAX or single-page update
            page.wait_for_timeout(3000)

        # Check if login failed or stayed on login page
        if "login.aspx" in page.url.lower():
            logging.error("Still on login page after postback. Extracting page content...")
            # Capture any error messages displayed on the screen
            page_text = page.locator("body").inner_text()
            logging.error(f"Page text excerpt: {page_text[:300].strip()}")
            page.screenshot(path="debug_login_failed.png")
            raise Exception("Authentication failed or page remained on login.aspx. Check GDV_MEMBER_ID and GDV_PASSWORD secrets.")

        # 2. Navigate to search page
        dest_link = page.query_selector("a:has-text('Destinations'), a[href*='Search'], #ctl00_lbDestinations")
        if dest_link:
            dest_link.click()
            page.wait_for_load_state("domcontentloaded", timeout=15000)

        # 3. Target the Month Filter Button
        month_selector = "a[id*='lbFilter']"
        
        try:
            logging.info("Waiting for target month button...")
            page.wait_for_selector(month_selector, timeout=20000)
            page.click(month_selector, force=True)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            logging.info("Successfully selected month filter!")
        except Exception as err:
            logging.error(f"Failed to find month filter. Current URL: {page.url}")
            page.screenshot(path="debug_search_page.png")
            raise err

        # 4. Scrape inventory cards
        page.wait_for_timeout(3000)  # Allow ASP.NET postback grid update to render
        listings = page.query_selector_all(".condo-item, .search-result-item, .resort-card")
        logging.info(f"Found {len(listings)} total listings on the page.")

        for listing in listings:
            try:
                title = listing.inner_text().strip()
                week_id = title.replace("\n", " ")[:60]

                if week_id and week_id not in seen_weeks:
                    seen_weeks.add(week_id)
                    new_weeks.append({
                        "title": week_id,
                        "dates": TARGET_MONTH_LABEL,
                        "resort": "GDV Property",
                        "unit": "Standard"
                    })
            except Exception as e:
                logging.warning(f"Error parsing listing item: {e}")

        browser.close()

    if new_weeks:
        logging.info(f"Found {len(new_weeks)} new weeks!")
        send_email_notification(new_weeks)
        save_seen_weeks(seen_weeks)
    else:
        logging.info("No new GDV inventory found.")

if __name__ == "__main__":
    run_gdv_scrape()