"""
Techno GBG — Event Crawler
Scrapes progek.se for upcoming Gothenburg events, writes new ones to Google Sheets.
"""

import os
import re
import datetime
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import json

# ── Config ──────────────────────────────────────────────────────────────────
SHEET_NAME = "Techno GBG Events"
WORKSHEET  = "Events"
SCOPES     = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

TODAY = datetime.date.today()

SWEDISH_MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4,
    "maj": 5, "juni": 6, "juli": 7, "augusti": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}
ENGLISH_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
ALL_MONTHS = {**SWEDISH_MONTHS, **ENGLISH_MONTHS}

# ── Google Sheets ────────────────────────────────────────────────────────────
def get_sheet():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).worksheet(WORKSHEET)

def ensure_headers(sheet):
    expected = ["Event name", "Date", "Date (raw)", "Source", "Genre hint", "Suggested song", "Status", "Added"]
    if sheet.row_values(1) != expected:
        sheet.update("A1", [expected])

def existing_keys(sheet):
    return {f"{r['Event name'].lower()}|{r['Date (raw)']}" for r in sheet.get_all_records()}

# ── Helpers ──────────────────────────────────────────────────────────────────
def fmt_date(d):
    return d.strftime("%d-%m-%y")

def parse_date(text):
    """
    Parse date from progek event text. Handles:
      "Fredag 15 maj", "Lördag 30 maj", "Saturday May 2nd", "Friday June 26th"
    Year assumed current, bumped to next year if already passed.
    """
    t = text.lower().strip()
    # Remove ordinal suffixes: 2nd -> 2
    t = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", t)

    # Try: digits then month word
    m = re.search(r"(\d{1,2})\s+([a-zåäö]+)", t)
    if m:
        day, month_str = int(m.group(1)), m.group(2)
        month = ALL_MONTHS.get(month_str)
        if month:
            year = TODAY.year
            try:
                d = datetime.date(year, month, day)
                if d < TODAY:
                    d = datetime.date(year + 1, month, day)
                return d
            except ValueError:
                pass

    # Try: month word then digits
    m = re.search(r"([a-zåäö]+)\s+(\d{1,2})", t)
    if m:
        month_str, day = m.group(1), int(m.group(2))
        month = ALL_MONTHS.get(month_str)
        if month:
            year = TODAY.year
            try:
                d = datetime.date(year, month, day)
                if d < TODAY:
                    d = datetime.date(year + 1, month, day)
                return d
            except ValueError:
                pass

    return None

def clean_name(text):
    """Strip date/weekday parts, return uppercase event name."""
    t = text.strip()
    weekdays = (
        r"(måndag|tisdag|onsdag|torsdag|fredag|lördag|söndag|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    )
    # Remove weekday
    t = re.sub(weekdays, "", t, flags=re.IGNORECASE).strip(",: ")
    # Remove trailing date pattern like "15 maj" or "May 2"
    t = re.sub(r"\d{1,2}\s+\w+$", "", t, flags=re.IGNORECASE).strip(",: ")
    t = re.sub(r"\w+\s+\d{1,2}$", "", t, flags=re.IGNORECASE).strip(",: ")
    # Remove ordinals
    t = re.sub(r"\d+(st|nd|rd|th)", "", t).strip(",: ")
    return t.upper() if t else ""

def guess_genre(text):
    text = text.lower()
    for genre, kws in [
        ("dnb",        ["drum and bass", "dnb", "drum & bass"]),
        ("psytrance",  ["psytrance", "psy", "psychedelic", "goa"]),
        ("melodic",    ["melodic", "afro", "organic"]),
        ("deep house", ["deep house", "deep tech"]),
        ("techno",     ["techno", "industrial", "ebm"]),
    ]:
        if any(k in text for k in kws):
            return genre
    return "techno"

def suggest_song(genre):
    return {
        "techno":     "Ben Klock – Subzero",
        "melodic":    "Innellea – Moana",
        "deep house": "Kerri Chandler – Atmosphere",
        "psytrance":  "Astrix – Red Means Distortion",
        "dnb":        "Noisia – Machine Gun (Camo & Krooked Remix)",
    }.get(genre, "Ben Klock – Subzero")

# ── Scraper ───────────────────────────────────────────────────────────────────
def scrape_progek():
    """
    Finds the 'Kommande fester med gästlista' section on progek.se,
    collects all linked events in the tables that follow,
    stops before 'Nedanstående arrangörer har använt gästlista'.
    """
    events = []
    resp = requests.get("https://progek.se", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")

    # Find the bold marker tag
    marker = None
    for tag in soup.find_all(["b", "strong"]):
        if "kommande fester" in tag.get_text().lower():
            marker = tag
            break

    if not marker:
        print("[progek] Section marker not found — page may have changed")
        return events

    stop = "nedanstående arrangörer har använt"

    # Walk forward through tables until stop text
    for table in marker.find_all_next("table"):
        if stop in table.get_text().lower():
            break
        for link in table.find_all("a"):
            raw = link.get_text(" ", strip=True)
            if not raw:
                continue
            date = parse_date(raw)
            if not date:
                print(f"[progek] No date in: '{raw}' — skipping")
                continue
            name = clean_name(raw)
            if not name:
                # Fall back to URL slug
                slug = link.get("href", "").strip("/").split("/")[-1]
                name = slug.upper().replace("-", " ")
            genre = guess_genre(raw)
            events.append({
                "name":   name,
                "date":   date,
                "source": "progek.se",
                "genre":  genre,
                "song":   suggest_song(genre),
            })
            print(f"[progek] {name} — {date}")

    print(f"[progek] {len(events)} upcoming events found")
    return events

# ── Write to sheet ────────────────────────────────────────────────────────────
def write_to_sheet(events):
    sheet = get_sheet()
    ensure_headers(sheet)
    existing = existing_keys(sheet)
    new_rows = []
    for e in sorted(events, key=lambda x: x["date"]):
        key = f"{e['name'].lower()}|{e['date'].isoformat()}"
        if key in existing:
            print(f"[sheet] Already exists: {e['name']}")
            continue
        new_rows.append([
            e["name"],
            fmt_date(e["date"]),
            e["date"].isoformat(),
            e["source"],
            e["genre"],
            e["song"],
            "pending",
            TODAY.isoformat(),
        ])
    if not new_rows:
        print("[sheet] Nothing new to add.")
        return
    sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
    print(f"[sheet] Added {len(new_rows)} new event(s).")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"Techno GBG crawler — {TODAY}")
    events = scrape_progek()
    write_to_sheet(events)
    print("Done.")

if __name__ == "__main__":
    main()
