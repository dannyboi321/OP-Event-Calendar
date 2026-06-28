#!/usr/bin/env python3
"""
One Piece Card Game - Tournament Monitor + Calendar Data Generator

1. Fetches https://en.onepiece-cardgame.com/events/
2. Parses every event card across all four sections
3. Filters to ones tagged "Tournament" within the included sections
4. Diffs against seen.json -- sends a Discord webhook for anything new,
   with the link and full event info
5. Writes docs/events.json -- a flat list of ALL known tournaments (with
   best-effort parsed start/end dates) for the calendar webpage to render

docs/events.json is committed back to the repo, and docs/index.html (a
static calendar page, see calendar.html) reads it client-side. GitHub Pages
serves the docs/ folder for free.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

EVENTS_URL = "https://en.onepiece-cardgame.com/events/"
ROOT = Path(__file__).parent
STATE_FILE = ROOT / "seen.json"
DOCS_DIR = ROOT / "docs"
EVENTS_JSON_FILE = DOCS_DIR / "events.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

ALL_SECTIONS = ["Championship", "Official Events", "Shop Events", "Convention Events"]
_included_raw = os.environ.get("INCLUDED_SECTIONS", "").strip()
if _included_raw:
    INCLUDED_SECTIONS = {s.strip().lower() for s in _included_raw.split(",") if s.strip()}
else:
    INCLUDED_SECTIONS = {s.lower() for s in ALL_SECTIONS}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def load_seen() -> set:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return set(data.get("seen_urls", []))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_seen(seen: set) -> None:
    STATE_FILE.write_text(json.dumps({"seen_urls": sorted(seen)}, indent=2))


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_events(html: str):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    seen_hrefs_this_parse = set()
    current_section = "Events"

    for el in soup.find_all(["h2", "h3", "a"]):
        if el.name in ("h2", "h3"):
            text = clean_text(el.get_text())
            upper = text.upper()
            if "CHAMPIONSHIP" in upper:
                current_section = "Championship"
            elif "OFFICIAL EVENT" in upper:
                current_section = "Official Events"
            elif "SHOP EVENT" in upper:
                current_section = "Shop Events"
            elif "CONVENTION EVENT" in upper:
                current_section = "Convention Events"
            continue

        if el.name == "a":
            href = el.get("href", "")
            if not href or href.startswith("#"):
                continue
            if "/events/" not in href:
                continue
            full_url = href if href.startswith("http") else f"https://en.onepiece-cardgame.com{href}"
            full_url = full_url.split("#")[0]

            text = clean_text(el.get_text())
            if not text or len(text) < 5:
                continue
            if full_url.rstrip("/").endswith("/events"):
                continue
            if text.upper() in {"LEARN MORE", "FIND AN EVENT", "FIND RELATED EVENTS", "VIEW ALL EVENTS", "PAST EVENTS"}:
                continue
            if "Event Period:" not in text:
                continue
            if full_url in seen_hrefs_this_parse:
                continue
            seen_hrefs_this_parse.add(full_url)

            label_keywords = [
                "Side Event Tournament",
                "Official Shop Only Regularly Held Tournament",
                "Regularly Held Beginner",
                "Beginner Regularly Held Tournament",
                "Beginner Regularly Held",
                "Convention Event Demo",
                "Convention Event",
                "Tournament",
                "Regularly Held",
            ]
            label = ""
            rest = text
            for kw in label_keywords:
                if text.startswith(kw):
                    label = kw
                    rest = text[len(kw):].strip()
                    break

            period_idx = rest.find("Event Period:")
            title = rest[:period_idx].strip() if period_idx != -1 else rest.strip()

            period = ""
            m = re.search(r"Event Period:\s*(.*?)(?:\s*Regulation:|\s+[A-Z][a-z]+ (?:the|us|your|for|is|are|will))", text)
            if not m:
                m = re.search(r"Event Period:\s*(.*)", text)
            if m:
                period = clean_text(m.group(1))

            is_tournament = "tournament" in label.lower()

            events.append({
                "url": full_url,
                "title": title or text,
                "label": label,
                "section": current_section,
                "period": period,
                "is_tournament": is_tournament,
            })

    return events


def parse_period_to_dates(period: str):
    """
    Best-effort parse of the free-text "Event Period" string into
    (start_date, end_date) ISO strings (end_date may be None for open-ended
    or single-day-style periods). Handles patterns seen on the site:
      "August 2026 onwards"            -> start=2026-08-01, end=None
      "2026 onwards"                   -> start=2026-01-01, end=None
      "July 1 - September 30, 2026"    -> start=2026-07-01, end=2026-09-30
      "April 1 - June 30, 2026"        -> start=2026-04-01, end=2026-06-30
      "July 30 - August 2, 2026"       -> start=2026-07-30, end=2026-08-02
      "March 2026 onwards"             -> start=2026-03-01, end=None
    Returns (None, None) if nothing recognizable is found.
    """
    p = period.strip()

    # Pattern: single date "<Month> <Day>, <Year>" with NO range dash
    # (must check this before the range pattern below doesn't accidentally
    # match it; checking single-date first and requiring no dash is safest)
    if "-" not in p:
        m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})$", p)
        if m:
            mon, day, year = m.groups()
            mon_num = MONTHS.get(mon.lower())
            if mon_num:
                try:
                    d = datetime(int(year), mon_num, int(day)).date().isoformat()
                    return d, d
                except ValueError:
                    pass

    # Pattern: "<Month> <Day> - <Month> <Day>, <Year>" or same-month "<Month> <Day> - <Day>, <Year>"
    m = re.match(
        r"([A-Za-z]+)\s+(\d{1,2})\s*-\s*([A-Za-z]+)?\s*(\d{1,2}),?\s*(\d{4})",
        p,
    )
    if m:
        mon1, day1, mon2, day2, year = m.groups()
        mon1_num = MONTHS.get(mon1.lower())
        mon2_num = MONTHS.get(mon2.lower()) if mon2 else mon1_num
        if mon1_num and mon2_num:
            try:
                start = datetime(int(year), mon1_num, int(day1)).date().isoformat()
                end = datetime(int(year), mon2_num, int(day2)).date().isoformat()
                return start, end
            except ValueError:
                pass

    # Pattern: "<Month> <Year> onwards"
    m = re.match(r"([A-Za-z]+)\s+(\d{4})\s+onwards", p, re.IGNORECASE)
    if m:
        mon, year = m.groups()
        mon_num = MONTHS.get(mon.lower())
        if mon_num:
            try:
                start = datetime(int(year), mon_num, 1).date().isoformat()
                return start, None
            except ValueError:
                pass

    # Pattern: "<Year> onwards"
    m = re.match(r"(\d{4})\s+onwards", p, re.IGNORECASE)
    if m:
        year = m.group(1)
        try:
            start = datetime(int(year), 1, 1).date().isoformat()
            return start, None
        except ValueError:
            pass

    # Pattern: just "<Month> <Year>" with nothing else
    m = re.match(r"^([A-Za-z]+)\s+(\d{4})$", p)
    if m:
        mon, year = m.groups()
        mon_num = MONTHS.get(mon.lower())
        if mon_num:
            try:
                start = datetime(int(year), mon_num, 1).date().isoformat()
                return start, None
            except ValueError:
                pass

    # Pattern: "<Month> - <Month> <Year>" (month range, no day numbers),
    # e.g. "August - September 2026"
    m = re.match(r"^([A-Za-z]+)\s*-\s*([A-Za-z]+)\s+(\d{4})$", p)
    if m:
        mon1, mon2, year = m.groups()
        mon1_num = MONTHS.get(mon1.lower())
        mon2_num = MONTHS.get(mon2.lower())
        if mon1_num and mon2_num:
            try:
                start = datetime(int(year), mon1_num, 1).date().isoformat()
                end_year = int(year) if mon2_num >= mon1_num else int(year) + 1
                if mon2_num == 12:
                    end = datetime(end_year, 12, 31).date().isoformat()
                else:
                    from datetime import timedelta
                    end = (datetime(end_year, mon2_num + 1, 1).date() - timedelta(days=1)).isoformat()
                return start, end
            except ValueError:
                pass

    return None, None


def send_discord_notification(event: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        print(f"WARNING: DISCORD_WEBHOOK_URL not set. Would have notified: {event['title']} ({event['url']})")
        return

    embed = {
        "title": f"🏆 New Tournament: {event['title']}",
        "url": event["url"],
        "color": 0xE53935,
        "fields": [
            {"name": "Section", "value": event["section"], "inline": True},
            {"name": "Label", "value": event["label"] or "Tournament", "inline": True},
        ],
        "footer": {"text": "ONE PIECE CARD GAME Events Monitor"},
    }
    if event["period"]:
        embed["fields"].append({"name": "Event Period", "value": event["period"], "inline": False})

    payload = {
        "content": f"📣 New tournament posted: **{event['title']}**\n{event['url']}",
        "embeds": [embed],
    }

    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    if resp.status_code not in (200, 204):
        print(f"ERROR: Discord webhook returned {resp.status_code}: {resp.text}", file=sys.stderr)
    else:
        print(f"Notified Discord about: {event['title']}")


def write_calendar_json(all_tournaments: list) -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    calendar_events = []
    for e in all_tournaments:
        start, end = parse_period_to_dates(e["period"])
        calendar_events.append({
            "title": e["title"],
            "url": e["url"],
            "section": e["section"],
            "label": e["label"] or "Tournament",
            "period_text": e["period"],
            "start_date": start,
            "end_date": end,
        })
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "events": calendar_events,
    }
    EVENTS_JSON_FILE.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(calendar_events)} tournaments to {EVENTS_JSON_FILE}")


def main():
    print(f"Fetching {EVENTS_URL} ...")
    html = fetch_page(EVENTS_URL)

    all_events = parse_events(html)
    in_scope = [e for e in all_events if e["section"].lower() in INCLUDED_SECTIONS]
    tournaments = [e for e in in_scope if e["is_tournament"]]

    print(f"Monitoring sections: {', '.join(s for s in ALL_SECTIONS if s.lower() in INCLUDED_SECTIONS)}")
    print(f"Parsed {len(in_scope)} in-scope event cards, {len(tournaments)} tagged as Tournament.")

    seen = load_seen()
    new_events = [e for e in tournaments if e["url"] not in seen]

    if not new_events:
        print("No new tournament posts found.")
    else:
        print(f"Found {len(new_events)} new tournament post(s):")
        for e in new_events:
            print(f"  - [{e['section']}] {e['title']} -> {e['url']}")
            send_discord_notification(e)
            seen.add(e["url"])

    for e in in_scope:
        seen.add(e["url"])

    save_seen(seen)

    # Regenerate the calendar data file with every known in-scope tournament
    write_calendar_json(tournaments)

    print("Done.")


if __name__ == "__main__":
    main()
