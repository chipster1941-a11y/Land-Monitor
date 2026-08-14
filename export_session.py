from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # Launch using your local Windows profile folder
    context = p.chromium.launch_persistent_context(
        user_data_dir="fb_user_data",
        headless=False
    )
    # Export the decrypted session cookies into a portable JSON file
    context.storage_state(path="storage_state.json")
    print("Successfully exported storage_state.json!")