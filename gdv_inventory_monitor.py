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
TARGET_MONTH_LABEL = "September 2027 / Priority Florida Regions (US Only)"
TARGET_YEAR = "2027"

# Expanded Priority keywords that bypass date restrictions
PRIORITY_LOCATIONS = [
    # Florida Keys
    "florida keys",
    "key west",
    "key largo",
    "marathon",
    "islamorada",
    "big pine key",
    # Gulf Coast / Southwest Florida
    "sanibel",
    "captiva",
    "marco island",
    "naples",
    "fort myers beach",
    "bonita springs",
    "estero"
]

# Non-US keywords to explicitly exclude
NON_US_KEYWORDS = [
    "dominican republic",
    "puerto plata",
    "punta cana",
    "mexico",
    "cancun",
    "cabo",
    "cozumel",
    "playa del carmen",
    "aruba",
    "bahamas",
    "jamaica",
    "sint maarten",
    "st. maarten",
    "costa rica",
    "belize",
    "canada",
    "barbados"
]

# US State abbreviations/names commonly found in listing addresses
US_STATE_PATTERNS = [
    r",\s*AL\b", r",\s*AK\b", r",\s*AZ\b", r",\s*AR\b", r",\s*CA\b", r",\s*CO\b", r",\s*CT\b", r",\s*DE\b",
    r",\s*FL\b", r",\s*GA\b", r",\s*HI\b", r",\s*ID\b", r",\s*IL\b", r",\s*IN\b", r",\s*IA\b", r",\s*KS\b",
    r",\s*KY\b", r",\s*LA\b", r",\s*ME\b", r",\s*MD\b", r",\s*MA\b", r",\s*MI\b", r",\s*MN\b", r",\s*MS\b",
    r",\s*MO\b", r",\s*MT\b", r",\s*NE\b", r",\s*NV\b", r",\s*NH\b", r",\s*NJ\b", r",\s*NM\b", r",\s*NY\b",
    r",\s*NC\b", r",\s*ND\b", r",\s*OH\b", r",\s*OK\b", r",\s*OR\b", r",\s*PA\b", r",\s*RI\b", r",\s*SC\b",
    r",\s*SD\b", r",\s*TN\b", r",\s*TX\b", r",\s*UT\b", r",\s*VT\b", r",\s*VA\b", r",\s*WA\b", r",\s*WV\b",
    r",\s*WI\b", r",\s*WY\b", r"\bUSA\b", r"\bUnited States\b"
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
    """Cleans up raw extracted text by removing clutter like 'View Resort' buttons."""
    text = raw_text.replace("View Resort", "").replace("VIEW RESORT", "").strip()
    clean = " ".join(text.split())
    return clean


def extract_checkin_year(text):
    """Extracts the check-in year from the listing text (e.g., Check-In: Sat 09/19/26 -> 2026)."""
    match = re.search(r"Check-In:\s*\w*\s*(\d{2})/(\d{2})/(\d{2})", text, re.IGNORECASE)
    if match:
        year_two_digits = match.group(3)
        return f"20{year_two_digits}"
    return None


def is_priority_location(text):
    """Checks if the listing matches any high-value location keywords."""
    lower_text = text.lower()
    return any(loc in lower_text for loc in PRIORITY_LOCATIONS)


def is_us_location(text):
    """Returns True if the listing appears to be in the US and not in international destinations."""
    lower_text = text.lower()
    
    # Exclude explicitly non-US destinations
    if any(keyword in lower_text for keyword in NON_US_KEYWORDS):
        return False
        
    # Check if text contains a priority Florida location or a US state pattern
    if is_priority_location(text):
        return True
        
    for pattern in US_STATE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
            
    # Default to True if no explicit non-US indicator was found
    return True


def send_email_notification(new_weeks):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        logging.warning("Email credentials not fully set. Skipping email alert.")
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = f"🚨 GDV Inventory Alert: {len(new_weeks)} New US Resort Listing(s) Found!"

    body_text = f"Global Discovery Vacations - New Inventory Alert\n"
    body_text += f"{'=' * 50}\n"
    body_text += f"Filter Mode: {TARGET_MONTH_LABEL}\n"
    body_text += f"Total New Listings Found: {len(new_weeks)}\n\n"
    
    for idx, item in enumerate(new_weeks, start=1):
        priority_tag = " [PRIORITY LOCATION MATCH]" if item.get("is_priority") else ""
        body_text += f"{idx}. {item['clean_title']}{priority_tag}\n"
        body_text += f"   --------------------------------------------------\n"

    body_text += f"\nLog into GDV Member Portal to view details: https://globaldiscoveryvacations.com/members.aspx\n"

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


def filter_by_2027_months(page):
    """Interacts with the Bootstrap Month dropdown control to click a 2027 anchor and trigger AJAX update."""
    try:
        logging.info("Attempting to open 'Month' dropdown control...")
        
        month_button = page.locator("button, div, a").filter(has_text=re.compile(r"^\s*Month\s*$", re.I)).first
        if month_button.count() == 0:
            month_button = page.locator(":text('Month')").first

        if month_button.count() > 0:
            month_button.click()
            page.wait_for_timeout(1500)

            target_anchors = page.locator("ul.dropdown-menu a, .dropdown-menu a, a").filter(has_text=re.compile(r"2027"))
            count = target_anchors.count()
            logging.info(f"Found {count} 2027 anchor links in dropdown menu.")

            if count > 0:
                sep_2027 = target_anchors.filter(has_text=re.compile(r"September,\s*2027", re.I))
                
                if sep_2027.count() > 0:
                    chosen_link = sep_2027.first
                    logging.info("Selected 'September, 2027' option from menu.")
                else:
                    chosen_link = target_anchors.first
                    logging.info("Selected first available 2027 option from menu.")

                chosen_link.click(force=True)
                page.wait_for_timeout(6000)
                logging.info("Clicked 2027 month filter link and waited for DOM update.")
            else:
                logging.warning("Opened Month dropdown but found no anchor tags containing '2027'.")
        else:
            logging.warning("Could not find the 'Month' dropdown trigger button.")
    except Exception as e:
        logging.error(f"Error while interacting with Month filter: {e}")


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

        # 3. Trigger 2027 Month Filter
        filter_by_2027_months(page)
        dismiss_modals(page)

        # 4. Locate elements based on 'View Resort' action buttons
        logging.info("Searching for resort containers via 'View Resort' action links...")
        
        view_resort_links = page.locator("a:has-text('View Resort')")
        link_count = view_resort_links.count()
        logging.info(f"Found {link_count} 'View Resort' listing triggers on page.")

        parsed_items = []

        if link_count > 0:
            for i in range(link_count):
                try:
                    container = view_resort_links.nth(i).locator("xpath=ancestor::div[contains(@class, 'col-') or contains(@class, 'card') or contains(@class, 'item') or contains(@class, 'resort')][1]")
                    
                    if container.count() == 0:
                        container = view_resort_links.nth(i).locator("xpath=..")

                    card_text = container.inner_text().strip()
                    cleaned = clean_resort_text(card_text)
                    if len(cleaned) > 10:
                        parsed_items.append(cleaned)
                except Exception as e:
                    logging.warning(f"Error extracting resort card index {i}: {e}")

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
                    for j in range(loc.count()):
                        txt = clean_resort_text(loc.nth(j).inner_text())
                        if len(txt) > 15:
                            parsed_items.append(txt)
                    if parsed_items:
                        break

        logging.info(f"Successfully extracted {len(parsed_items)} resort listing cards.")

        # 5. Evaluate new listings with conditional location & US checks
        for item_text in parsed_items:
            # Check US-only requirement
            if not is_us_location(item_text):
                logging.info(f"Skipping non-US listing: {item_text[:40]}...")
                continue

            checkin_year = extract_checkin_year(item_text)
            has_priority_loc = is_priority_location(item_text)

            # Keep if it matches priority locations OR if it matches 2027 check-in year
            if not has_priority_loc and checkin_year and checkin_year != TARGET_YEAR:
                logging.info(f"Skipping non-priority listing with Check-In year {checkin_year}: {item_text[:40]}...")
                continue

            item_id = item_text[:100]

            if item_id not in seen_weeks:
                seen_weeks.add(item_id)
                new_weeks.append({
                    "clean_title": item_text,
                    "dates": TARGET_MONTH_LABEL,
                    "is_priority": has_priority_loc
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