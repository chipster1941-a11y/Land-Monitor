Set-Content -Path "inspect_gdv.py" -Value 'import os
import re
from playwright.sync_api import sync_playwright

GDV_MEMBER_ID = os.environ.get("GDV_MEMBER_ID", "")
GDV_PASSWORD = os.environ.get("GDV_PASSWORD", "")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    print("Logging into GDV...")
    page.goto("https://globaldiscoveryvacations.com/login.aspx")
    page.locator("input[type=\"text\"], input[type=\"email\"]").first.fill(GDV_MEMBER_ID)
    page.locator("input[type=\"password\"]").first.fill(GDV_PASSWORD)
    page.locator("input[type=\"password\"]").first.press("Enter")
    page.wait_for_timeout(5000)

    print("Navigating to Condos Search...")
    page.goto("https://globaldiscoveryvacations.com/condos/Condos.aspx")
    page.wait_for_timeout(5000)

    print("\n=== SEARCH FORM CONTROLS ===")
    inputs = page.locator("input, select, button")
    for i in range(inputs.count()):
        elem = inputs.nth(i)
        tag = elem.evaluate("el => el.tagName.toLowerCase()")
        e_id = elem.get_attribute("id") or ""
        e_name = elem.get_attribute("name") or ""
        e_type = elem.get_attribute("type") or ""
        if e_id or e_name:
            print(f"[{tag.upper()}] id=\"{e_id}\" | name=\"{e_name}\" | type=\"{e_type}\"")

    print("\n=== 2027 DROPDOWN LINKS ===")
    month_btn = page.locator("button, div, a").filter(has_text=re.compile(r"^\s*Month\s*$", re.I)).first
    if month_btn.count() > 0:
        month_btn.click()
        page.wait_for_timeout(1500)
        
        links = page.locator("a").filter(has_text=re.compile(r"2027"))
        print(f"Found {links.count()} 2027 links:")
        for i in range(min(links.count(), 10)):
            link = links.nth(i)
            txt = link.inner_text().strip()
            href = link.get_attribute("href") or ""
            onclick = link.get_attribute("onclick") or ""
            print(f"  Link #{i+1}: Text=\"{txt}\" | href=\"{href}\" | onclick=\"{onclick}\"")

    browser.close()
' -Encoding utf8

python inspect_gdv.py