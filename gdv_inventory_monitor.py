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

        page.wait_for_timeout(3000)

        logging.info("Locating input fields dynamically...")

        target_frame = page
        user_input = None
        pass_input = None

        frames_to_check = [page] + page.frames
        for frame in frames_to_check:
            txt_loc = frame.locator("input[type='text'], input[type='email'], input:not([type])").filter(has_not_text="")
            pwd_loc = frame.locator("input[type='password']")

            if txt_loc.count() > 0 and pwd_loc.count() > 0:
                target_frame = frame
                user_input = txt_loc.first
                pass_input = pwd_loc.first
                break

        if not user_input or not pass_input:
            page.screenshot(path="debug_login_missing_inputs.png")
            page_text = page.locator("body").inner_text()
            logging.error(f"Page text excerpt: {page_text[:400].replace(chr(10), ' ').strip()}")
            raise Exception("Could not locate username/password fields on page or subframes.")

        logging.info("Filling credentials into detected fields...")
        user_input.fill(clean_member_id)
        pass_input.fill(clean_password)

        logging.info("Submitting login form via Enter press...")
        try:
            with page.expect_navigation(timeout=20000):
                pass_input.press("Enter")
        except Exception:
            page.wait_for_timeout(4000)

        if "login.aspx" in page.url.lower():
            logging.info("Executing click fallback on submit elements...")
            login_btn = target_frame.locator("input[type='submit'], button[type='submit'], input[value*='Login'], a:has-text('Login')").first
            if login_btn.is_visible():
                try:
                    with page.expect_navigation(timeout=15000):
                        login_btn.click()
                except Exception:
                    page.wait_for_timeout(4000)

        if "login.aspx" in page.url.lower():
            page.screenshot(path="debug_login_failed.png")
            page_text = page.locator("body").inner_text()
            logging.error(f"Page text excerpt: {page_text[:400].replace(chr(10), ' ').strip()}")
            raise Exception("Authentication failed on member login.aspx.")

        logging.info(f"Member login successful! Current URL: {page.url}")

        # 2. Target the CONDOS dropdown/section
        logging.info("Navigating into CONDOS section...")
        condos_link = page.locator("a:has-text('CONDOS'), a[href*='condo'], a[href*='Condo']").first

        if condos_link.is_visible():
            condos_link.hover()
            page.wait_for_timeout(1000)

            # Check if hover revealed a sub-menu link like "Search Condos" or "Availability"
            sub_link = page.locator("a:has-text('Search'), a:has-text('Availability'), a:has-text('Browse'), a[href*='search']").first
            if sub_link.is_visible():
                logging.info("Clicking sub-menu link revealed under CONDOS...")
                sub_link.click()
            else:
                logging.info("Clicking main CONDOS header link...")
                condos_link.click()
            page.wait_for_timeout(4000)

        # Direct navigation fallback if still on base members page
        if "members.aspx" in page.url.lower():
            logging.info("Attempting direct route fallback to /condos.aspx...")
            page.goto("https://globaldiscoveryvacations.com/condos.aspx", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

        logging.info(f"Current inventory URL: {page.url}")

        # 3. Trigger Search Form / Grid Load if present
        search_triggers = [
            "input[value*='Search']",
            "button:has-text('Search')",
            "a:has-text('Search')",
            "input[id*='btnSearch']",
            "input[id*='btnSubmit']",
            "a[id*='lbSearch']"
        ]

        for trigger_sel in search_triggers:
            btn = page.locator(trigger_sel).first
            if btn.is_visible():
                logging.info(f"Triggering search button via '{trigger_sel}'...")
                try:
                    btn.click()
                    page.wait_for_timeout(4000)
                except Exception as e:
                    logging.warning(f"Error triggering search button: {e}")
                break

        # 4. Scrape Cards / Inventory Rows
        card_selectors = [
            ".condo-item",
            ".search-result-item",
            ".resort-card",
            ".inventory-item",
            ".grid-item",
            ".resort",
            "tr.rgRow",
            "tr.rgAltRow",
            "div[class*='resort']",
            "div[class*='inventory']",
            "div[class*='condo']",
            "div[class*='card']"
        ]

        listings = []
        for selector in card_selectors:
            found = page.query_selector_all(selector)
            if len(found) > 0:
                logging.info(f"Matched {len(found)} elements using selector '{selector}'")
                listings = found
                break

        if len(listings) == 0:
            page.screenshot(path="debug_condos_search.png")
            page_text = page.locator("body").inner_text()
            logging.info(f"Page text snippet: {page_text[:400].replace(chr(10), ' ').strip()}")

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