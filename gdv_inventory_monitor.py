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

        # 2. Direct route to Condos search endpoint with networkidle wait
        logging.info("Navigating directly to Condos search endpoint...")
        page.goto("https://globaldiscoveryvacations.com/condos/Condos.aspx", wait_until="networkidle", timeout=30000)
        
        # Extended wait to allow ASP.NET AJAX rendering
        logging.info("Waiting 7 seconds for AJAX search form controls to render...")
        page.wait_for_timeout(7000)

        dismiss_modals(page)

        # 3. Traversal across all frames (main page + iframes) to locate interactive search controls
        logging.info("Scanning all frames for search form controls...")
        all_frames = [page] + page.frames
        
        search_frame = page

        for idx, frame in enumerate(all_frames):
            selects = frame.locator("select")
            inputs = frame.locator("input, button, a")
            
            sel_count = selects.count()
            input_count = inputs.count()
            logging.info(f"Frame #{idx} ('{frame.name}') contains {sel_count} <select> and {input_count} interactive inputs.")
            
            if sel_count > 0:
                search_frame = frame
                for s_i in range(sel_count):
                    s_elem = selects.nth(s_i)
                    s_id = s_elem.get_attribute("id") or f"sel_{s_i}"
                    opts = s_elem.locator("option")
                    opt_list = [opts.nth(o_i).inner_text().strip() for o_i in range(min(opts.count(), 8))]
                    logging.info(f"  Dropdown [{s_id}] options: {opt_list}")

        # 4. Trigger Search / Postback Button across all detected buttons
        search_triggers = [
            "input[value*='Search']",
            "input[value*='Filter']",
            "button:has-text('Search')",
            "a:has-text('Search')",
            "input[id*='btnSearch']",
            "a[id*='lbSearch']",
            "input[type='submit']"
        ]

        for trigger in search_triggers:
            btn = search_frame.locator(trigger).first
            if btn.is_visible():
                logging.info(f"Triggering search button '{trigger}' inside target frame...")
                try:
                    btn.click(force=True)
                    page.wait_for_timeout(6000)
                except Exception as e:
                    logging.warning(f"Error clicking search trigger: {e}")
                break

        # 5. Extract Grid / Table Items across all frames
        card_selectors = [
            "table[id*='Grid'] tr",
            "table[id*='rg'] tr",
            "tr.rgRow",
            "tr.rgAltRow",
            ".condo-item",
            ".search-result-item",
            ".resort-card",
            ".inventory-item",
            "table tr",
            "div[class*='resort']",
            "div[class*='inventory']",
            "div[class*='card']"
        ]

        listings = []
        for frame in all_frames:
            for selector in card_selectors:
                found = frame.query_selector_all(selector)
                if len(found) > 0:
                    logging.info(f"Matched {len(found)} elements in frame using selector '{selector}'")
                    listings = found
                    break
            if len(listings) > 0:
                break

        if len(listings) == 0:
            logging.info("No listings parsed. Saving debug artifacts (HTML snapshot + screenshot)...")
            page.screenshot(path="debug_condos_search_results.png")
            with open("debug_condos_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())

        for listing in listings:
            try:
                title = listing.inner_text().strip()
                week_id = " ".join(title.split())[:80]

                if week_id and len(week_id) > 10 and week_id not in seen_weeks:
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