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


def dismiss_modals(page):
    """Detects and closes popup modals interrupting navigation."""
    try:
        page.evaluate("""
            () => {
                const backdrops = document.querySelectorAll('.modal-backdrop, .modal');
                backdrops.forEach(el => el.remove());
                document.body.classList.remove('modal-open');
                document.body.style.overflow = 'auto';
            }
        """)
    except Exception:
        pass


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
        page.goto("https://globaldiscoveryvacations.com/login.aspx", wait_until="networkidle", timeout=60000)

        clean_member_id = GDV_MEMBER_ID.strip().strip("'\"") if GDV_MEMBER_ID else ""
        clean_password = GDV_PASSWORD.strip().strip("'\"") if GDV_PASSWORD else ""

        page.wait_for_timeout(3000)

        user_input = None
        pass_input = None

        frames_to_check = [page] + page.frames
        for frame in frames_to_check:
            txt_loc = frame.locator("input[type='text'], input[type='email'], input:not([type])").filter(has_not_text="")
            pwd_loc = frame.locator("input[type='password']")

            if txt_loc.count() > 0 and pwd_loc.count() > 0:
                user_input = txt_loc.first
                pass_input = pwd_loc.first
                break

        if not user_input or not pass_input:
            raise Exception("Could not locate username/password fields.")

        user_input.fill(clean_member_id)
        pass_input.fill(clean_password)

        try:
            with page.expect_navigation(timeout=20000):
                pass_input.press("Enter")
        except Exception:
            page.wait_for_timeout(5000)

        logging.info(f"Member login successful! Current URL: {page.url}")
        dismiss_modals(page)

        # 2. Navigate directly to Condos search endpoint
        logging.info("Navigating to Condos search endpoint...")
        page.goto("https://globaldiscoveryvacations.com/condos/Condos.aspx", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(6000)
        dismiss_modals(page)

        # 3. Locate elements based on 'View Resort' action buttons
        logging.info("Searching for resort containers via 'View Resort' action links...")
        
        # Primary strategy: find all 'View Resort' links and pull their parent container text
        view_resort_links = page.locator("a:has-text('View Resort')")
        link_count = view_resort_links.count()
        logging.info(f"Found {link_count} 'View Resort' listing triggers on page.")

        parsed_items = []

        if link_count > 0:
            for i in range(link_count):
                try:
                    # Get enclosing card/row container element
                    container = view_resort_links.nth(i).locator("xpath=ancestor::div[contains(@class, 'col-') or contains(@class, 'card') or contains(@class, 'item') or contains(@class, 'resort')][1]")
                    
                    if container.count() == 0:
                        # Fallback to direct parent paragraph or div
                        container = view_resort_links.nth(i).locator("xpath=..")

                    card_text = container.inner_text().strip()
                    # Clean up whitespace
                    clean_text = " ".join(card_text.split())
                    if len(clean_text) > 10:
                        parsed_items.append(clean_text)
                except Exception as e:
                    logging.warning(f"Error extracting resort card index {i}: {e}")

        # Fallback strategy: selector scan if anchor parent extraction yields nothing
        if not parsed_items:
            fallback_selectors = [
                ".thumbnail",
                ".caption",
                "div[class*='resort']",
                "div[class*='condo']",
                ".panel-body"
            ]
            for selector in fallback_selectors:
                loc = page.locator(selector)
                if loc.count() > 0:
                    logging.info(f"Fallback selector '{selector}' matched {loc.count()} items.")
                    for j in range(loc.count()):
                        txt = " ".join(loc.nth(j).inner_text().split())
                        if "View Resort" in txt and len(txt) > 15:
                            parsed_items.append(txt)
                    if parsed_items:
                        break

        logging.info(f"Successfully extracted {len(parsed_items)} resort listing cards.")

        # 4. Evaluate new listings
        for item_text in parsed_items:
            # First 100 characters serve as a distinct fingerprint
            item_id = item_text[:100]

            if item_id not in seen_weeks:
                seen_weeks.add(item_id)
                new_weeks.append({
                    "title": item_text,
                    "dates": TARGET_MONTH_LABEL
                })

        browser.close()

    if new_weeks:
        logging.info(f"Found {len(new_weeks)} new GDV listings!")
        send_email_notification(new_weeks)
        save_seen_weeks(seen_weeks)
    else:
        logging.info("No new GDV inventory found.")


if __name__ == "__main__":
    run_gdv_scrape()