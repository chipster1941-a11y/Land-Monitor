#!/usr/bin/env python3
"""
timeshare_resale_monitor.py
Monitors timeshare resale listings, HOA direct lists, and PDF announcements.
Scores listings based on exchange trading power, maintenance-to-value ratio,
and natural disaster / special assessment risk.
"""

import os
import re
import json
import logging
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import requests

# PDF Parsing Support
try:
    import pypdf
except ImportError:
    pypdf = None

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Configuration File Paths
SEEN_FILE = "seen_timeshares.json"

# Target Regions and Scoring Weights
HIGH_TRADING_POWER_LOCATIONS = {
    "hawaii": ["maui", "kauai", "oahu", "honolulu", "kona"],
    "ski": ["breckenridge", "vail", "park city", "aspen", "lake tahoe", "steamboat"],
    "coastal_ca": ["newport coast", "carlsbad", "monterey", "avila beach"],
    "east_coast_beach": ["hilton head", "outer banks", "myrtle beach", "cape cod"]
}

HIDDEN_GEM_LOCATIONS = [
    "sedona", "scottsdale", "white mountains", "lincoln nh", 
    "duck nc", "kitty hawk", "incline village", "bar Harbor", "kennebunkport"
]

DILUTED_LOCATIONS = ["orlando", "kissimmee", "las vegas", "branson", "williamsburg"]

HIGH_VALUE_WEEKS = {
    "ski_peak": list(range(1, 11)),
    "summer_peak": list(range(24, 33)),
    "foliage_peak": list(range(38, 42)),
    "holiday_weeks": [51, 52]
}

COASTAL_RISK_ZONES = ["florida", "fl", "south carolina", "sc", "north carolina", "nc", "gulf"]


class TimeshareMonitor:
    def __init__(self, seen_filepath: str = SEEN_FILE):
        self.seen_filepath = seen_filepath
        self.seen_ids = self._load_seen_ids()

    def _load_seen_ids(self) -> set:
        """Loads previously processed listing IDs, supporting both list and dict JSON structures."""
        if os.path.exists(self.seen_filepath):
            try:
                with open(self.seen_filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return set(data)
                    elif isinstance(data, dict):
                        return set(data.get("seen_ids", []))
            except Exception as e:
                logging.error(f"Error loading {self.seen_filepath}: {e}")
                return set()
        return set()

    def save_seen_ids(self):
        """Persists seen IDs back to local storage."""
        try:
            with open(self.seen_filepath, "w", encoding="utf-8") as f:
                json.dump({"seen_ids": list(self.seen_ids)}, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving {self.seen_filepath}: {e}")

    def evaluate_trading_power(self, listing: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculates a score (0-100) based on location, unit size, week number,
        and flags potential hidden gems or assessment risks.
        """
        score = 50
        flags = []

        location = listing.get("location", "").lower()
        resort_name = listing.get("resort_name", "").lower()
        title_desc = f"{resort_name} {location} {listing.get('description', '')}".lower()
        week = listing.get("week_number")
        maint_fee = listing.get("maintenance_fee", 0.0)
        unit_bedrooms = listing.get("bedrooms", 1)

        # 1. Location Evaluation
        is_high_demand = False
        for category, loc_list in HIGH_TRADING_POWER_LOCATIONS.items():
            if any(loc in title_desc for loc in loc_list):
                score += 20
                is_high_demand = True
                flags.append(f"High Demand Area ({category})")
                break

        if any(gem in title_desc for gem in HIDDEN_GEM_LOCATIONS):
            score += 25
            flags.append("Hidden Gem Region")

        if any(diluted in title_desc for diluted in DILUTED_LOCATIONS):
            score -= 25
            flags.append("Diluted Market (High Supply)")

        # 2. Week Analysis
        if isinstance(week, int):
            if week in HIGH_VALUE_WEEKS["holiday_weeks"]:
                score += 20
                flags.append("Holiday Week (Weeks 51-52)")
            elif week in HIGH_VALUE_WEEKS["ski_peak"] and "ski" in flags:
                score += 15
                flags.append("Peak Ski Week")
            elif week in HIGH_VALUE_WEEKS["summer_peak"]:
                score += 15
                flags.append("Peak Summer Week")
            elif week in HIGH_VALUE_WEEKS["foliage_peak"]:
                score += 10
                flags.append("Peak Foliage Week")

        # 3. Unit Size & Capacity
        if unit_bedrooms >= 2:
            score += 10
            flags.append(f"{unit_bedrooms}-Bed Unit")
        if "lock-off" in title_desc or "lockoff" in title_desc:
            score += 10
            flags.append("Lock-off Unit")

        # 4. Assessment and Risk Checking (Using regex word boundaries to prevent false matches)
        coastal_pattern = r"\b(florida|fl|south carolina|sc|north carolina|nc|gulf)\b"
        if re.search(coastal_pattern, title_desc, re.IGNORECASE):
            flags.append("Coastal Hurricane Hazard Zone")

        active_levy_keywords = ["assessment pending", "special assessment", "roof assessment", "owner levy"]
        if any(kw in title_desc for kw in active_levy_keywords):
            score -= 30
            flags.append("WARNING: Active or Pending Levy/Special Assessment")

        # 5. Maintenance Fee Efficiency Ratio
        if maint_fee > 0 and maint_fee < 1000 and (is_high_demand or "Hidden Gem Region" in flags):
            score += 10
            flags.append("Low Maintenance Fee (< $1,000)")

        # Final Score Cap
        listing["trading_power_score"] = max(0, min(100, score))
        listing["analysis_flags"] = flags
        return listing

    def parse_hoa_pdf_list(self, pdf_path_or_url: str) -> List[Dict[str, Any]]:
        """Parses HOA direct resale inventory published as PDF files."""
        extracted_listings = []
        if not pypdf:
            logging.warning("pypdf not installed. Skipping PDF parsing.")
            return extracted_listings

        try:
            # Download if URL, open if file path
            if pdf_path_or_url.startswith("http"):
                resp = requests.get(pdf_path_or_url, timeout=10)
                temp_pdf = "temp_hoa_list.pdf"
                with open(temp_pdf, "wb") as f:
                    f.write(resp.content)
                reader = pypdf.PdfReader(temp_pdf)
            else:
                reader = pypdf.PdfReader(pdf_path_or_url)

            text_content = ""
            for page in reader.pages:
                text_content += page.extract_text() + "\n"

            # Simple regex parser for structured line items (e.g., Unit 102 - Week 28 - $500 - Fee: $850)
            pattern = re.compile(
                r"(?:Unit|Resort)\s*(?P<unit>\w+)?.*?Week\s*(?P<week>\d+).*?\$?\s*(?P<price>\d[\d,]*).*?Fee:?\s*\$?\s*(?P<fee>\d[\d,]*)",
                re.IGNORECASE
            )

            for match in pattern.finditer(text_content):
                item = match.groupdict()
                listing_id = f"hoa_pdf_{item.get('unit')}_{item.get('week')}"
                
                extracted_listings.append({
                    "id": listing_id,
                    "resort_name": "HOA Direct PDF Listing",
                    "location": "HOA Direct",
                    "week_number": int(item["week"]) if item.get("week") else None,
                    "price": float(item["price"].replace(",", "")) if item.get("price") else 0.0,
                    "maintenance_fee": float(item["fee"].replace(",", "")) if item.get("fee") else 0.0,
                    "bedrooms": 2,  # Default fallback
                    "source": "HOA_PDF",
                    "description": text_content[:200]
                })

        except Exception as e:
            logging.error(f"Error parsing HOA PDF {pdf_path_or_url}: {e}")

        return extracted_listings

    def process_and_filter_listings(self, listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filters duplicates, scores remaining listings, and saves seen states."""
        valuable_listings = []

        for listing in listings:
            listing_id = str(listing.get("id"))
            if listing_id in self.seen_ids:
                continue

            # Evaluate properties
            analyzed = self.evaluate_trading_power(listing)
            self.seen_ids.add(listing_id)

            # Filter threshold for alerts
            if analyzed["trading_power_score"] >= 65:
                valuable_listings.append(analyzed)

        self.save_seen_ids()
        return valuable_listings


if __name__ == "__main__":
    monitor = TimeshareMonitor()

    # Example Mock Data simulating extracted listings from web scrapers or HOA PDF lists
    sample_raw_listings = [
        {
            "id": "ts_101",
            "resort_name": "Barrier Island Station",
            "location": "Duck, Outer Banks, NC",
            "week_number": 28,
            "bedrooms": 2,
            "price": 500.00,
            "maintenance_fee": 850.00,
            "description": "HOA Direct Deed transfer. Fixed peak summer beach week."
        },
        {
            "id": "ts_102",
            "resort_name": "Orlando Sun Vacation Club",
            "location": "Orlando, FL",
            "week_number": 12,
            "bedrooms": 2,
            "price": 1.00,
            "maintenance_fee": 1100.00,
            "description": "Close to theme parks. Annual float week."
        },
        {
            "id": "ts_103",
            "resort_name": "Hyatt Piñon Pointe",
            "location": "Sedona, AZ",
            "week_number": 40,
            "bedrooms": 2,
            "price": 1200.00,
            "maintenance_fee": 920.00,
            "description": "Red Rock views. Includes lock-off option. Special assessment pending for roof repair."
        }
    ]

    print("Running Timeshare Resale Monitor...\n")
    results = monitor.process_and_filter_listings(sample_raw_listings)

    for item in results:
        print(f"[{item['trading_power_score']}/100] {item['resort_name']} - {item['location']}")
        print(f" Price: ${item['price']} | Fee: ${item['maintenance_fee']} | Week: {item['week_number']}")
        print(f" Flags: {', '.join(item['analysis_flags'])}")
        print("-" * 60)