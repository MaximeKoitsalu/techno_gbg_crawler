"""
Techno GBG — Event Crawler
Scrapes progek.se for upcoming events, writes new ones to Google Sheets.
"""

import os
import re
import datetime
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import json

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

ALL_MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4,
    "maj": 5, "juni": 6, "juli": 7, "augusti": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
    "january": 1, "february": 2, "march": 3,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "october": 10,
}

# ── Google Sheets ─────────────────────────────────────────────────────────────
def get_sheet():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).worksheet(WORKSHEET)

def ensure_headers(sheet):
    expected = ["Event name", "Date", "Date (raw)", "Source", "Genre hint", "Suggested song", "Status", "Added"]
    if sheet.row_values(1) != expected:
        sheet.update(range_name="A1", values=[expected])

def existing_keys(sheet):
    return {f"{r['Event name'].lower()}|{r['Date (raw)']}" for r in sheet.get_all_records()}

# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_date(d):
    return d.strftime("%d-%m-%y")

def parse_date(text):
    t = text.lower().strip()
    t = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", t)
    # digits then month word: "15 maj", "2 may"
    m = re.search(r"(\d{1,2})\s+([a-z]+)", t)
    if m:
        day, mon = int(m.group(1)), m.group(2)
        month = ALL_MONTHS.get(mon)
        if month:
            year = TODAY.year
            try:
                d = datetime.date(year, month, day)
                return datetime.date(year + 1, month, day) if d < TODAY else d
            except ValueError:
                pass
    # month word then digits: "may 2", "june 26"
    m = re.search(r"([a-z]+)\s+(\d{1,2})", t)
    if m:
        mon, day = m.group(1), int(m.group(2))
        month = ALL_MONTHS.get(mon)
        if month:
            year = TODAY.year
            try:
                d = datetime.date(year, month, day)
                return datetime.date(year + 1, month, day) if d < TODAY else d
            except ValueError:
                pass
    return None

def clean_name(text):
    t = text.strip()
    # Remove Swedish/English weekday names
    t = re.sub(
        r"\b(måndag|tisdag|onsdag|torsdag|fredag|lördag|söndag|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        "", t, flags=re.IGNORECASE
    )
    # Remove date patterns like "15 maj", "May 2nd", "June 26"
    t = re.sub(r"\b\d{1,2}(st|nd|rd|th)?\s+[a-zA-ZåäöÅÄÖ]+\b", "", t)
    t = re.sub(r"\b[a-zA-ZåäöÅÄÖ]+\s+\d{1,2}(st|nd|rd|th)?\b", "", t)
    # Remove leftover punctuation
    t = re.sub(r"[,:]+", "", t).strip()
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t.upper()

def guess_genre(text):
    t = text.lower()
    for genre, kws in [
        ("dnb",        ["drum and bass", "dnb", "drum & bass"]),
        ("psytrance",  ["psytrance", "psy", "psychedelic", "goa"]),
        ("melodic",    ["melodic", "afro", "organic"]),
        ("deep house", ["deep house", "deep tech"]),
        ("techno",     ["techno", "industrial", "ebm"]),
    ]:
        if any(k in t for k in kws):
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
    events = []
    resp = requests.get("https://progek.se", headers=HEADERS, timeout=15)
    resp.raise_for_status()

    # Force Windows-1252 encoding — progek.se uses this, not UTF-8
    resp.encoding = "windows-1252"
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")

    # Find the bold "Kommande fester" marker
    marker = None
    for tag in soup.find_all(["b", "strong"]):
        if "kommande fester" in tag.get_text().lower():
            marker = tag
            break

    if not marker:
        print("[progek] Could not find 'Kommande fester' section")
        print("[progek] Page text snippet:", soup.get_text()[:500])
        return events

    stop = "nedanstående arrangörer har använt"

    for table in marker.find_all_next("table"):
        if stop in table.get_text().lower():
            break
        for link in table.find_all("a"):
            raw = link.get_text(" ", strip=True)
            if not raw:
                continue
            date = parse_date(raw)
            if not date:
                print(f"[progek] No date found in: '{raw}' — skipping")
                continue
            name = clean_name(raw)
            if not name:
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
            print(f"[progek] Found: {name} — {date}")

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
