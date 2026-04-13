"""
Techno GBG — Event Crawler
Scrapes RA and progek.se, deduplicates, writes new events to Google Sheets.
"""

import os
import re
import time
import datetime
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import json

# ── Config ─────────────────────────────────────────────────────────────────
SHEET_NAME   = "Techno GBG Events"   # name of your Google Sheet
WORKSHEET    = "Events"              # tab name inside the sheet
SCOPES       = [
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

# ── Google Sheets auth ──────────────────────────────────────────────────────
def get_sheet():
    creds_json = os.environ["GOOGLE_CREDENTIALS"]
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).worksheet(WORKSHEET)
    return sheet


def ensure_headers(sheet):
    first_row = sheet.row_values(1)
    expected = ["Event name", "Date", "Date (raw)", "Source", "Genre hint", "Suggested song", "Status", "Added"]
    if first_row != expected:
        sheet.update("A1", [expected])


def existing_keys(sheet):
    """Return a set of 'name|date' strings already in the sheet."""
    records = sheet.get_all_records()
    return {f"{r['Event name'].lower()}|{r['Date (raw)']}" for r in records}


# ── Helpers ─────────────────────────────────────────────────────────────────
def fmt_date(d: datetime.date) -> str:
    """DD-MM-YY display format."""
    return d.strftime("%d-%m-%y")


def raw_date(d: datetime.date) -> str:
    """YYYY-MM-DD for dedup key."""
    return d.isoformat()


def guess_genre(text: str) -> str:
    text = text.lower()
    for genre, keywords in [
        ("dnb",        ["drum and bass", "dnb", "drum & bass"]),
        ("psytrance",  ["psytrance", "psy trance", "psychedelic trance"]),
        ("melodic",    ["melodic", "afro house", "organic"]),
        ("deep house", ["deep house", "deep tech"]),
        ("techno",     ["techno", "industrial", "ebm", "hard techno"]),
    ]:
        if any(k in text for k in keywords):
            return genre
    return "techno"  # default


def suggest_song(genre: str) -> str:
    suggestions = {
        "techno":     "Ben Klock – Subzero",
        "melodic":    "Innellea – Moana",
        "deep house": "Kerri Chandler – Atmosphere",
        "psytrance":  "Astrix – Red Means Distortion",
        "dnb":        "Noisia – Machine Gun (Camo & Krooked Remix)",
    }
    return suggestions.get(genre, "Ben Klock – Subzero")


# ── Scraper: Resident Advisor ───────────────────────────────────────────────
def scrape_ra() -> list[dict]:
    """
    Scrape RA events listing for Gothenburg.
    RA area ID 45 = Gothenburg/Göteborg.
    """
    events = []
    url = "https://ra.co/events/se/gothenburg"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[RA] Request failed: {e}")
        return events

    soup = BeautifulSoup(resp.text, "html.parser")

    # RA renders server-side event listings in <article> or <li> tags
    # with data attributes — try multiple selectors as RA updates their markup
    articles = (
        soup.select("article[data-testid='event-item']") or
        soup.select("li[data-testid='event-item']") or
        soup.select("article.event-item") or
        soup.select("[class*='eventItem']")
    )

    for article in articles:
        try:
            # Event title
            title_el = (
                article.select_one("h3") or
                article.select_one("h2") or
                article.select_one("[class*='title']")
            )
            if not title_el:
                continue
            name = title_el.get_text(strip=True).upper()

            # Date — look for time element or date string
            time_el = article.select_one("time")
            if time_el and time_el.get("datetime"):
                raw = time_el["datetime"][:10]  # YYYY-MM-DD
                event_date = datetime.date.fromisoformat(raw)
            else:
                # try to find a date string in text
                date_text = article.get_text()
                date_match = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", date_text)
                if not date_match:
                    continue
                try:
                    event_date = datetime.datetime.strptime(
                        date_match.group(0), "%d %B %Y"
                    ).date()
                except ValueError:
                    continue

            if event_date < TODAY:
                continue

            genre = guess_genre(article.get_text())
            events.append({
                "name":   name,
                "date":   event_date,
                "source": "RA",
                "genre":  genre,
                "song":   suggest_song(genre),
            })
        except Exception as e:
            print(f"[RA] Parse error: {e}")
            continue

    print(f"[RA] Found {len(events)} upcoming events")
    return events


# ── Scraper: progek.se ──────────────────────────────────────────────────────
def scrape_progek() -> list[dict]:
    events = []
    url = "https://progek.se/kalender/"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[progek] Request failed: {e}")
        return events

    soup = BeautifulSoup(resp.text, "html.parser")

    # progek uses WordPress-style event listings
    items = (
        soup.select(".event") or
        soup.select(".tribe-event") or
        soup.select("article.type-tribe_events") or
        soup.select("[class*='event']")
    )

    for item in items:
        try:
            title_el = item.select_one("h2, h3, .tribe-event-url, [class*='title']")
            if not title_el:
                continue
            name = title_el.get_text(strip=True).upper()

            # Look for date in time tag or abbr
            time_el = item.select_one("time, abbr[class*='dtstart'], [class*='start-date']")
            if time_el:
                raw = (time_el.get("datetime") or time_el.get("title") or "")[:10]
                try:
                    event_date = datetime.date.fromisoformat(raw)
                except ValueError:
                    continue
            else:
                # fallback: scan text for Swedish date patterns (e.g. "12 april 2026")
                SWEDISH_MONTHS = {
                    "januari": 1, "februari": 2, "mars": 3, "april": 4,
                    "maj": 5, "juni": 6, "juli": 7, "augusti": 8,
                    "september": 9, "oktober": 10, "november": 11, "december": 12
                }
                text = item.get_text().lower()
                m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text)
                if not m:
                    continue
                day, month_str, year = m.groups()
                month = SWEDISH_MONTHS.get(month_str)
                if not month:
                    continue
                event_date = datetime.date(int(year), month, int(day))

            if event_date < TODAY:
                continue

            genre = guess_genre(item.get_text())
            events.append({
                "name":   name,
                "date":   event_date,
                "source": "progek.se",
                "genre":  genre,
                "song":   suggest_song(genre),
            })
        except Exception as e:
            print(f"[progek] Parse error: {e}")
            continue

    print(f"[progek] Found {len(events)} upcoming events")
    return events


# ── Write to Google Sheets ──────────────────────────────────────────────────
def write_to_sheet(events: list[dict]):
    if not events:
        print("No new events to write.")
        return

    sheet = get_sheet()
    ensure_headers(sheet)
    existing = existing_keys(sheet)

    new_rows = []
    for e in events:
        key = f"{e['name'].lower()}|{raw_date(e['date'])}"
        if key in existing:
            continue
        new_rows.append([
            e["name"],
            fmt_date(e["date"]),
            raw_date(e["date"]),
            e["source"],
            e["genre"],
            e["song"],
            "pending",
            TODAY.isoformat(),
        ])

    if not new_rows:
        print("All events already in sheet.")
        return

    sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
    print(f"Added {len(new_rows)} new events to sheet.")


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    print(f"Running scraper — {TODAY}")
    all_events = []
    all_events.extend(scrape_ra())
    time.sleep(2)
    all_events.extend(scrape_progek())

    # Sort by date
    all_events.sort(key=lambda e: e["date"])

    write_to_sheet(all_events)
    print("Done.")


if __name__ == "__main__":
    main()
