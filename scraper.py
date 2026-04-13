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
    expected = ["Event name", "Date", "Date (raw)", "Source", "Signup URL", "Genre hint", "Suggested song", "Status", "Added"]
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
    # digits then month word: "15 maj", "2 may", "1 augusti"
    m = re.search(r"(\d{1,2})\s+([a-z]+)", t)
    if m:
        day, mon = int(m.group(1)), m.group(2)
        month = ALL_MONTHS.get(mon)
        if month:
            try:
                d = datetime.date(TODAY.year, month, day)
                return datetime.date(TODAY.year + 1, month, day) if d < TODAY else d
            except ValueError:
                pass
    # month word then digits: "may 2", "june 26"
    m = re.search(r"([a-z]+)\s+(\d{1,2})", t)
    if m:
        mon, day = m.group(1), int(m.group(2))
        month = ALL_MONTHS.get(mon)
        if month:
            try:
                d = datetime.date(TODAY.year, month, day)
                return datetime.date(TODAY.year + 1, month, day) if d < TODAY else d
            except ValueError:
                pass
    return None

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
    """
    Exact HTML structure (confirmed):
      <td valign="top">
        <a href="https://progek.se/frequency" target="_blank"><img ...></a>
        <p style="font-size: small">Frequency: CURRENT VALUE<br>Saturday May 2nd</p>
      </td>

    Strategy: find all <p style="font-size: small"> tags.
    Each contains event name (before <br>) and date (after <br>).
    The signup URL is on the <a> tag in the same <td>.
    Stop when we hit the "Nedanst" paragraph.
    """
    events = []
    resp = requests.get("https://progek.se", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = "iso-8859-1"  # page declares charset=iso-8859-1
    soup = BeautifulSoup(resp.text, "html.parser")

    for p in soup.find_all("p", style=lambda s: s and "font-size: small" in s):
        # Get the two text parts split by <br>
        parts = p.get_text("\n").strip().split("\n")
        parts = [x.strip() for x in parts if x.strip()]
        if len(parts) < 2:
            continue

        name_raw = parts[0]
        date_raw = parts[1]

        # Strip city prefix like "Göteborg, "
        name_clean = re.sub(r"^[A-ZÅÄÖ][a-zåäö]+,\s*", "", name_raw).strip()
        name = name_clean.upper()

        date = parse_date(date_raw)
        if not date:
            print(f"[progek] Could not parse date '{date_raw}' for '{name}' — skipping")
            continue

        # Get signup URL from the <a> in the parent <td>
        td = p.find_parent("td")
        signup_url = ""
        if td:
            a = td.find("a", href=True)
            if a:
                signup_url = a["href"]

        genre = guess_genre(name_raw)
        events.append({
            "name":       name,
            "date":       date,
            "source":     "progek.se",
            "signup_url": signup_url,
            "genre":      genre,
            "song":       suggest_song(genre),
        })
        print(f"[progek] Found: {name} — {date} — {signup_url}")

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
            e["signup_url"],
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