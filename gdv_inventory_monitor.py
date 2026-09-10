import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

# Logging Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Environment Variables
GDV_MEMBER_ID = os.getenv("GDV_MEMBER_ID")
GDV_PASSWORD = os.getenv("GDV_PASSWORD")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

SEEN_WEEKS_FILE = "seen_gdv_weeks.json"
TARGET_MONTH_LABEL = "Fall 2027"


def load_seen_weeks():
    if os.path.exists(SEEN_WEEKS_FILE):
        try:
            with open(SEEN_WEEKS_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Error loading seen weeks: {e}")
    return set()


def save_seen_weeks(seen_weeks):
    try:
        with open(SEEN_WEEKS_FILE, "w") as f:
            json.dump(list(seen_weeks), f, indent=2)
        logging.info("Saved updated seen weeks state.")
    except Exception as e:
        logging.error(f"Error saving seen weeks: {e}")


def send_email_notification(new_weeks):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        logging.warning("Email credentials not fully set. Skipping email alert.")
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = f"🚨 New GDV Inventory Alert: {len(new_weeks)} New Listing(s)!"

    body_text = f"Found {len(new_weeks)} new availability match(es) for {TARGET_MONTH_LABEL}:\n\n"
    for item in new_weeks:
        body_text += f"• {item['title']}\n"

    msg.attach(MIMEText(body_text, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.quit()
        logging.info("Email notification sent successfully!")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")


def run_gdv_scrape():
    if not GDV_MEMBER_ID or not GDV_PASSWORD:
        logging.error("GDV credentials missing from environment variables.")
        return

    seen_weeks = load_seen_weeks()
    new_weeks = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # 1. Login to Member Portal
        logging.info("Navigating to GDV Member Portal...")
        page.goto("https://globaldiscoveryvacations.com/login.aspx", wait_until="domcontentloaded", timeout=60000)

        clean_member_id = GDV_MEMBER_ID.strip().strip("'\"") if GDV_MEMBER_ID else ""
        clean_password = GDV_PASSWORD.strip().strip("'\"") if GDV_PASSWORD else ""

        logging.info("Locating exact member input elements...")
        
        # Specific ASP.NET Member login field selectors
        user_input = page.locator("#ctl00_body_tbUsername, input[id*='tbUsername'], input[name*='tbUsername']").first
        pass_input = page.locator("#ctl00_body_tbPassword, input[id*='tbPassword'], input[name*='tbPassword']").first
        login_btn = page.locator("#ctl00_body_btnLogin, input[id*='btnLogin'], input[value='Login']").first

        user_input.wait_for(state="visible", timeout=20000)
        user_input.fill(clean_member_id)
        pass_input.fill(clean_password)

        logging.info("Submitting login form via Enter key & click trigger...")
        try:
            with page.expect_navigation(timeout=25000):
                # Pressing enter inside the password field triggers ASP.NET form postback reliably
                pass_input.press("Enter")
        except Exception:
            page.wait_for_timeout(5000)

        # Fallback click if still on login page
        if "login.aspx" in page.url.lower() and login_btn.is_visible():
            logging.info("Attempting direct button click fallback...")
            try:
                with page.expect_navigation(timeout=20000):
                    login_btn.click()
            except Exception:
                page.wait_for_timeout(5000)

        if "login.aspx" in page.url.lower():
            page.screenshot(path="debug_login_failed.png")
            page_text = page.locator("body").inner_text()
            logging.error(f"Page text excerpt: {page_text[:400].replace(chr(10), ' ').strip()}")
            raise Exception("Authentication failed on member login.aspx.")

        logging.info(f"Member login successful! Current URL: {page.url}")

        # 2. Locate and navigate to Search/Destinations
        search_selectors = [
            "a:has-text('Destinations')",
            "a:has-text('Search')",
            "a:has-text('Resorts')",
            "a[href*='Search']",
            "a[href*='Destinations']",
            "#ctl00_lbDestinations"
        ]
        
        for sel in search_selectors:
            if page.query_selector(sel):
                logging.info(f"Found navigation link using selector: {sel}")
                page.click(sel)
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                break

        # 3. Handle Month/Filter Postback
        page.wait_for_timeout(4000)
        filter_button = page.query_selector("a[id*='lbFilter'], input[id*='btnSearch'], button[id*='Search']")
        if filter_button:
            logging.info("Triggering search filter postback...")
            filter_button.click()
            page.wait_for_timeout(4000)

        # 4. Scrape All Grid Cards
        card_selectors = ".condo-item, .search-result-item, .resort-card, .inventory-item, .grid-item, tr.rgRow, tr.rgAltRow"
        listings = page.query_selector_all(card_selectors)
        logging.info(f"Scraped {len(listings)} matching listing elements.")

        if len(listings) == 0:
            page.screenshot(path="debug_empty_search.png")

        for listing in listings:
            try:
                title = listing.inner_text().strip()
                week_id = " ".join(title.split())[:80]

                if week_id and week_id not in seen_weeks:
                    seen_weeks.add(week_id)
                    new_weeks.append({
                        "title": week_id,
                        "dates": TARGET_MONTH_LABEL
                    })
            except Exception as e:
                logging.warning(f"Error parsing item: {e}")

        browser.close()

    if new_weeks:
        logging.info(f"Found {len(new_weeks)} new GDV listings!")
        send_email_notification(new_weeks)
        save_seen_weeks(seen_weeks)
    else:
        logging.info("No new GDV inventory found.")


if __name__ == "__main__":
    run_gdv_scrape()