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
TARGET_MONTH_LABEL = "Jan/Feb 2027 & Priority FL Regions (US Only)"
TARGET_YEAR = "2027"

# Priority keywords that bypass strict date restrictions
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
    "estero",
    # Southeast Florida / Miami Metro Area
    "miami",
    "miami beach",
    "south beach",
    "fort lauderdale",
    "ft. lauderdale",
    "pompano beach",
    "hollywood",
    "boca raton",
    "delray beach",
    "west palm beach",
    "sunny isles",
    "key biscayne"
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

# Comprehensive US location patterns
US_STATE_PATTERNS = [
    # Wildcard state matches
    r"\bnorth\s+carolina.*",
    r"\bsouth\s+carolina.*",
    r"\bvirginia.*",
    r"\bflorida.*",
    r"\bgeorgia.*",
    r"\btennessee.*",
    r"\bmichigan.*",
    r"\bwisconsin.*",
    
    # Generic state sub-region pattern (matches "State Name - SubRegion")
    r"\b[A-Za-z\s]+-\s*[A-Za-z\s]+\b",
    
    # Standalone 2-letter state codes and full names
    r"\bNC\b", r"\bFL\b", r"\bSC\b", r"\bVA\b", r"\bTN\b", r"\bGA\b",
    r"\bMA\b", r"massachusetts", r"\bNH\b", r"new hampshire", r"\bMO\b", r"missouri",
    r"\bOR\b", r"oregon", r"\bID\b", r"idaho", r"\bIN\b", r"indiana", r"\bMI\b", r"\bWI\b",
    
    # Standard comma-separated state abbreviations (e.g. "Outer Banks, NC")
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
    text = raw_text.replace("View Resort", "").replace("VIEW RESORT", "").strip()
    return " ".join(text.split())


def extract_checkin_year(text):
    match = re.search(r"Check-In:\s*\w*\s*(\d{2})/(\d{2})/(\d{2})", text, re.IGNORECASE)
    if match:
        year_two_digits = match.group(3)
        return f"20{year_two_digits}"
    return None


def is_priority_location(text):
    lower_text = text.lower()
    return any(loc in lower_text for loc in PRIORITY_LOCATIONS)


def is_us_location(text):
    lower_text = text.lower()
    
    if any(keyword in lower_text for keyword in NON_US_KEYWORDS):
        return False
        
    if is_priority_location(text):
        return True
        
    for pattern in US_STATE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
            
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
    """Triggers ASP.NET PostBack directly for January and February 2027 controls."""
    try:
        logging.info("Attempting to trigger ASP.NET PostBack for Jan/Feb 2027...")

        # 1. First attempt direct client-side execution using exact WebForms target IDs
        target_ids = [
            "ctl00$cphMemberBody$rpMonth$ctl05$lbMonth",  # January 2027
            "ctl00$cphMemberBody$rpMonth$ctl06$lbMonth"   # February 2027
        ]

        # Execute PostBack for January 2027 directly
        logging.info(f"Invoking __doPostBack for January 2027 ({target_ids[0]})...")
        page.evaluate(f"__doPostBack('{target_ids[0]}', '');")
        
        # Wait for ASP.NET AJAX UpdatePanel to finish response
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(4000)
        logging.info("Completed ASP.NET PostBack request for 2027 inventory.")

    except Exception as e:
        logging.error(f"Error triggering 2027 month PostBack: {e}")


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
                    container = view_resort_links.nth(i).locator("xpath=ancestor::div[contains(@class, 'col-') or contains(@class, 'card') or contains(@class, 'item') or contains(@class, 'resort')][1]")
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

        # 2. Condos search endpoint
        logging.info("Navigating to Condos search endpoint...")
        page.goto("https://globaldiscoveryvacations.com/condos/Condos.aspx", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(6000)
        dismiss_modals(page)

        # 3. Trigger Month Filter
        filter_by_2027_months(page)
        dismiss_modals(page)

        # 4. Extract listings
        parsed_items = extract_all_pages_inventory(page)
        logging.info(f"Successfully extracted {len(parsed_items)} total resort listing cards across pagination.")

        # 5. Evaluate listings
        for item_text in parsed_items:
            if not is_us_location(item_text):
                logging.info(f"Skipping non-US listing: {item_text[:40]}...")
                continue

            checkin_year = extract_checkin_year(item_text)
            has_priority_loc = is_priority_location(item_text)

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