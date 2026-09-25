import os
import json
import logging
import smtplib
import re
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

# Logging Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Environment Variables (with fallback support for GitHub Actions)
GDV_MEMBER_ID = os.getenv("GDV_MEMBER_ID") or os.getenv("GDV_USER")
GDV_PASSWORD = os.getenv("GDV_PASSWORD") or os.getenv("GDV_PASS")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") or os.getenv("EMAIL_PASS")
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
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
                elif isinstance(data, dict):
                    return set(data.keys())
        except Exception as e:
            logging.error(f"Error loading seen weeks: {e}")
    return set()


def save_seen_weeks(seen_weeks):
    try:
        with open(SEEN_WEEKS_FILE, "w") as f:
            json.dump(sorted(list(seen_weeks)), f, indent=2)
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
    return not any(keyword in lower_text for keyword in NON_US_KEYWORDS)


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


def select_month_and_search(page, target_month_str):
    """
    Navigates to the GDV Condos portal and triggers the ASP.NET postback
    for the selected month dropdown option.
    """
    condos_url = "https://globaldiscoveryvacations.com/condos/Condos.aspx"
    
    try:
        logging.info(f"Navigating to condo portal for {target_month_str}...")
        page.goto(condos_url, wait_until="networkidle", timeout=30000)
        dismiss_modals(page)

        month_btn = page.locator("button:has-text('Month'), .dropdown-toggle:has-text('Month')").first
        month_btn.wait_for(state="visible", timeout=10000)

        if month_btn.is_visible():
            logging.info(f"Opening month dropdown for {target_month_str}...")
            month_btn.click()
            page.wait_for_timeout(1000)

            # Target the lbMonth LinkButton inside rpMonth dropdown items
            month_option = page.locator(f"a[id*='lbMonth']:has-text('{target_month_str}')").first
            
            if month_option.count() > 0 and month_option.is_visible():
                logging.info(f"Clicking lbMonth LinkButton for {target_month_str}...")
                
                # Execute __doPostBack directly if JavaScript href is present
                href = month_option.get_attribute("href")
                if href and href.startswith("javascript:"):
                    page.evaluate(href.replace("javascript:", ""))
                else:
                    month_option.click(force=True)

                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(3000)
            else:
                logging.warning(f"Could not locate visible lbMonth link for '{target_month_str}'")
        else:
            logging.warning("Month dropdown button was not visible on page.")

        dismiss_modals(page)

    except Exception as e:
        logging.warning(f"Error during month selection for {target_month_str}: {e}")

        dismiss_modals(page)

    except Exception as e:
        logging.warning(f"Error during month selection for {target_month_str}: {e}")


def extract_all_resort_cards(page, month_label):
    """
    Extracts resort listing cards across all available pagination pages
    for the selected month, with DOM structure logging on failure.
    """
    extracted_cards = []
    current_page = 1

    while True:
        logging.info(f"Extracting resort cards from Page {current_page} for {month_label}...")

        page.wait_for_timeout(1500)

        # 1. Primary selectors (cards, panels, grid rows, items)
        card_locators = page.locator(
            ".condo-item, .resort-card, .resort-item, [id*='pnlResort'], "
            "[id*='rpCondos'] > div, [id*='rpMonth'] > div, "
            "tr[id*='Row'], div[class*='col-']"
        )
        card_count = card_locators.count()

        # 2. Fallback: Find any element containing view/details links or standard text blocks
        if card_count == 0:
            card_locators = page.locator("div, tr, li").filter(
                has=page.locator("a, button, input[type='submit']").filter(has_text=["Details", "View", "Book", "Select", "More"])
            )
            card_count = card_locators.count()

        # 3. Diagnostic mode: If still 0, log the main container's DOM structure
        if card_count == 0:
            logging.warning(f"No card elements found on Page {current_page} for {month_label}.")
            try:
                dom_summary = page.evaluate("""() => {
                    const form = document.querySelector('form');
                    if (!form) return 'No form found on page';
                    
                    // Collect IDs and class names of main container elements
                    const elements = Array.from(form.querySelectorAll('div[id], table[id], ul, section'));
                    return elements.slice(0, 15).map(el => ({
                        tag: el.tagName,
                        id: el.id,
                        class: el.className,
                        textSnippet: el.innerText ? el.innerText.substring(0, 60).replace(/\\s+/g, ' ') : ''
                    }));
                }""")
                logging.info(f"DOM Structure Diagnostic for {month_label}: {dom_summary}")
            except Exception as diag_err:
                logging.debug(f"Could not run diagnostic: {diag_err}")
            break

        for i in range(card_count):
            try:
                card_text = card_locators.nth(i).inner_text().strip()
                if card_text and 15 < len(card_text) < 4000:
                    extracted_cards.append(card_text)
            except Exception as card_err:
                logging.warning(f"Error parsing card {i} on page {current_page}: {card_err}")

        # Check for pagination links
        next_button = page.locator(
            "a[id*='Next'], a[id*='lnkNext'], a[id*='lbNext'], "
            ".pagination a:has-text('Next'), .pagination a:has-text('>'), "
            "a[id*='DataPager']:has-text('>')"
        ).first

        if next_button.count() > 0 and next_button.is_visible():
            is_disabled = next_button.get_attribute("disabled") or "disabled" in (next_button.get_attribute("class") or "")
            if is_disabled:
                logging.info(f"Next button disabled. Reached end of pagination at Page {current_page}.")
                break

            current_page += 1
            logging.info(f"Navigating to Page {current_page} for {month_label}...")

            href = next_button.get_attribute("href")
            if href and href.startswith("javascript:"):
                page.evaluate(href.replace("javascript:", ""))
            else:
                next_button.click(force=True)

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2500)
        else:
            logging.info(f"No further pagination pages found after Page {current_page}.")
            break

    logging.info(f"Extracted total of {len(extracted_cards)} items across {current_page} page(s) for {month_label}.")
    return extracted_cards

import re

def extract_all_resort_cards(page, month_label):
    """
    Extracts individual resort listing cards across all available pagination pages
    for the selected month, isolating individual items and stripping boilerplate text.
    """
    extracted_cards = []
    current_page = 1

    while True:
        logging.info(f"Extracting resort cards from Page {current_page} for {month_label}...")
        page.wait_for_timeout(1500)

        # Target individual card/row containers directly (avoid broad top-level wrapper divs)
        cards = page.locator(
            "[id*='rpCondos'] > div, [id*='pnlResort'], .resort-card, .condo-item, tr[id*='Row']"
        )
        card_count = cards.count()

        # Fallback: find standard item columns inside the inventory grid
        if card_count == 0:
            cards = page.locator("div.panel, div.thumbnail, div.card").filter(
                has=page.locator("a[id*='Details'], a[id*='View'], a[href*='Resort'], a[href*='Condo']")
            )
            card_count = cards.count()

        if card_count == 0:
            logging.warning(f"No card elements found on Page {current_page} for {month_label}.")
            break

        logging.info(f"Found {card_count} individual card container(s) on Page {current_page}.")

        for i in range(card_count):
            try:
                raw_text = cards.nth(i).inner_text().strip()

                # Ignore top-level page headers/sidebars if caught in fallback
                if "Narrow Your Search" in raw_text or "Reserve Your Vacation Condo" in raw_text:
                    continue

                # Strip out excess whitespace and clean individual lines
                lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

                # Filter out pure navigation & layout controls
                ignore_phrases = [
                    "Reserve Your", "Vacation Condo", "Narrow Your Search",
                    "Vacation Type", "Bedrooms", "Protect Your Vacation",
                    "Membership Guidebook", "SORT BY", "MAP", "VIEW", "SHOW"
                ]
                cleaned_lines = [l for l in lines if not any(p in l for p in ignore_phrases)]

                clean_card_text = "\n".join(cleaned_lines)

                # Ensure it's a real resort listing with details (ID, Check-in, or Unit Size)
                if clean_card_text and len(clean_card_text) > 30:
                    if any(key in clean_card_text for key in ["ID", "Check-In", "Avail", "Bd", "Occ", "AS LOW AS"]):
                        extracted_cards.append(clean_card_text)

            except Exception as card_err:
                logging.warning(f"Error parsing card {i} on page {current_page}: {card_err}")

        # Check for pagination next button
        next_button = page.locator(
            "a[id*='Next'], a[id*='lnkNext'], a[id*='lbNext'], "
            ".pagination a:has-text('Next'), .pagination a:has-text('>')"
        ).first

        if next_button.count() > 0 and next_button.is_visible():
            is_disabled = next_button.get_attribute("disabled") or "disabled" in (next_button.get_attribute("class") or "")
            if is_disabled:
                break

            current_page += 1
            logging.info(f"Navigating to Page {current_page} for {month_label}...")

            href = next_button.get_attribute("href")
            if href and href.startswith("javascript:"):
                page.evaluate(href.replace("javascript:", ""))
            else:
                next_button.click(force=True)

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2500)
        else:
            break

    logging.info(f"Extracted total of {len(extracted_cards)} items across {current_page} page(s) for {month_label}.")
    return extracted_cards


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
            select_month_and_search(page, month_label)
            month_items = extract_all_resort_cards(page, month_label)
            
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