import os
import json
import logging
import smtplib
import re
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
TARGET_MONTH_LABEL = "Nov 2026 - Feb 2027 (FL) | Sept 2027 (MI, VA, TN, NC) | Priority FL Regions"

PRIORITY_LOCATIONS = [
    # Florida Keys
    "florida keys", "key west", "key largo", "marathon", "islamorada", "big pine key",
    # Gulf Coast / Southwest Florida
    "sanibel", "captiva", "marco island", "naples", "fort myers", "fort myers beach", "bonita springs", "estero",
    # Southeast Florida / Miami Metro Area
    "miami", "miami beach", "south beach", "fort lauderdale", "ft. lauderdale", "pompano beach",
    "hollywood", "boca raton", "delray beach", "west palm beach", "sunny isles", "key biscayne"
]

NON_US_KEYWORDS = [
    "dominican republic", "puerto plata", "punta cana", "mexico", "cancun", "cabo",
    "cozumel", "playa del carmen", "aruba", "bahamas", "jamaica", "sint maarten",
    "st. maarten", "costa rica", "belize", "canada", "barbados"
]

# Regional location matchers
FLORIDA_PATTERNS = [r"\bflorida\b", r"\bfl\b"]
SEPT_2027_STATES_PATTERNS = [
    r"\bmichigan\b", r"\bmi\b",
    r"\bvirginia\b", r"\bva\b",
    r"\btennessee\b", r"\btn\b",
    r"\bnorth carolina\b", r"\bnc\b"
]


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


def clean_resort_text(raw_text):
    text = raw_text.replace("View Resort", "").replace("VIEW RESORT", "").strip()
    return " ".join(text.split())


def is_priority_location(text):
    lower_text = text.lower()
    return any(loc in lower_text for loc in PRIORITY_LOCATIONS)


def is_us_location(text):
    lower_text = text.lower()
    if any(keyword in lower_text for keyword in NON_US_KEYWORDS):
        return False
    return True


def matches_patterns(text, patterns):
    lower_text = text.lower()
    return any(re.search(pat, lower_text) for pat in patterns)


def send_email_notification(new_weeks):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        logging.warning("Email credentials not fully set. Skipping email alert.")
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = f"🚨 GDV Inventory Alert: {len(new_weeks)} New Resort Listing(s) Found!"

    body_text = f"Global Discovery Vacations - New Inventory Alert\n"
    body_text += f"{'=' * 50}\n"
    body_text += f"Filter Mode: {TARGET_MONTH_LABEL}\n"
    body_text += f"Total New Listings Found: {len(new_weeks)}\n\n"
    
    for idx, item in enumerate(new_weeks, start=1):
        priority_tag = " [PRIORITY LOCATION MATCH]" if item.get("is_priority") else ""
        body_text += f"{idx}. {item['clean_title']}{priority_tag}\n"
        body_text += f"   --------------------------------------------------\n"

    body_text += "\nLog into GDV Member Portal to view details: https://globaldiscoveryvacations.com/members.aspx\n"

    msg.attach(MIMEText(body_text, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.quit()
        logging.info("Formatted email notification sent successfully!")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")


def dismiss_modals(page):
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


def extract_all_pages_inventory(page):
    all_parsed_items = []
    page_num = 1
    max_pages = 5

    while page_num <= max_pages:
        logging.info(f"Extracting resort cards from Page {page_num}...")
        
        view_resort_links = page.locator("a:has-text('View Resort')")
        link_count = view_resort_links.count()

        if link_count > 0:
            for i in range(link_count):
                try:
                    container = view_resort_links.nth(i).locator(
                        "xpath=ancestor::div[contains(@class, 'col-') or contains(@class, 'card') or contains(@class, 'item') or contains(@class, 'resort')][1]"
                    )
                    if container.count() == 0:
                        container = view_resort_links.nth(i).locator("xpath=..")

                    card_text = container.inner_text().strip()
                    cleaned = clean_resort_text(card_text)
                    if len(cleaned) > 10 and cleaned not in all_parsed_items:
                        all_parsed_items.append(cleaned)
                except Exception as e:
                    logging.warning(f"Error extracting card on page {page_num}: {e}")

        next_button = page.locator("a:has-text('Next'), .pagination a:has-text('>'), li.next a, a[aria-label='Next']").first
        if next_button.count() > 0 and next_button.is_visible():
            logging.info(f"Clicking Next page control (Page {page_num + 1})...")
            next_button.click(force=True)
            page.wait_for_timeout(5000)
            dismiss_modals(page)
            page_num += 1
        else:
            logging.info(f"No further pagination pages found after Page {page_num}.")
            break

    return all_parsed_items


def select_month_and_search(page, target_month_str):
    condos_url = "https://globaldiscoveryvacations.com/condos/Condos.aspx"
    page.goto(condos_url, wait_until="networkidle", timeout=30000)
    dismiss_modals(page)

    try:
        month_btn = page.locator("button:has-text('Month'), .dropdown-toggle:has-text('Month')").first
        if month_btn.is_visible():
            logging.info("Clicking Bootstrap Month dropdown button...")
            month_btn.click()
            page.wait_for_timeout(1000)

            # Try exact match first (e.g., "November, 2026"), then fall back to short month name
            parts = target_month_str.replace(",", "").split()
            full_month = parts[0] if len(parts) > 0 else ""
            short_month = full_month[:3]  # 'Nov'
            year = parts[1] if len(parts) > 1 else ""

            month_option = page.locator(
                f".dropdown-menu a:has-text('{target_month_str}'), "
                f".dropdown-menu li:has-text('{target_month_str}'), "
                f".dropdown-menu a:has-text('{short_month}'), "
                f"a:has-text('{target_month_str}')"
            ).first

            if month_option.is_visible():
                logging.info(f"Selecting option for {target_month_str}...")
                month_option.click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000)
            else:
                logging.warning(f"Could not find dropdown option text for '{target_month_str}'")
        else:
            logging.warning("Month dropdown button was not visible on page.")

        dismiss_modals(page)

    except Exception as e:
        logging.warning(f"Error during Bootstrap month selection: {e}")


def process_target_months(page):
    """Navigates to GDV Condo search, applies target dates, and extracts inventory."""
    target_months = [
        ("November, 2026", "fl_only"),
        ("December, 2026", "fl_only"),
        ("January, 2027", "fl_only"),
        ("February, 2027", "fl_only"),
        ("September, 2027", "sept_2027_states")
    ]
    
    combined_items = []
    
    for month_label, region_rule in target_months:
        try:
            logging.info(f"Navigating to condo portal and searching for {month_label}...")
            
            select_month_and_search(page, month_label)
            month_items = extract_all_pages_inventory(page)
            
            logging.info(f"Extracted {len(month_items)} items for {month_label}. Applying location filters...")

            for item_text in month_items:
                # 1. Block international listings
                if not is_us_location(item_text):
                    continue

                has_priority_loc = is_priority_location(item_text)

                # 2. Priority locations bypass standard state limits
                if has_priority_loc:
                    combined_items.append({"text": item_text, "is_priority": True})
                    continue

                # 3. Apply state restrictions based on target month rules
                if region_rule == "fl_only":
                    if matches_patterns(item_text, FLORIDA_PATTERNS):
                        combined_items.append({"text": item_text, "is_priority": False})
                elif region_rule == "sept_2027_states":
                    if matches_patterns(item_text, SEPT_2027_STATES_PATTERNS):
                        combined_items.append({"text": item_text, "is_priority": False})

        except Exception as e:
            logging.error(f"Error executing extraction for {month_label}: {e}")

    return combined_items


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

        # 1. Login
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

        # 2. Extract listings across target months with rules
        filtered_items = process_target_months(page)
        logging.info(f"Successfully filtered to {len(filtered_items)} matching resort listings across all criteria.")

        # 3. Deduplicate against seen weeks
        for item in filtered_items:
            item_text = item["text"]
            item_id = item_text[:100]

            if item_id not in seen_weeks:
                seen_weeks.add(item_id)
                new_weeks.append({
                    "clean_title": item_text,
                    "dates": TARGET_MONTH_LABEL,
                    "is_priority": item["is_priority"]
                })

        browser.close()

    if new_weeks:
        logging.info(f"Found {len(new_weeks)} new GDV listings matching criteria!")
        send_email_notification(new_weeks)
        save_seen_weeks(seen_weeks)
    else:
        logging.info("No new matching GDV inventory found.")


if __name__ == "__main__":
    run_gdv_scrape()