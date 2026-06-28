#!/usr/bin/env python3
"""
One Piece Card Game - Tournament Event Monitor
Watches https://en.onepiece-cardgame.com/events/ for new posts labeled
"Tournament" (anywhere in their category label, e.g. "Tournament",
"Side Event Tournament", "Official Shop Only Regularly Held Tournament")
across all four sections (Championship, Official Events, Shop Events,
Convention Events), and posts new ones to a Discord webhook.

State (which event URLs have already been seen) is stored in seen.json.
On GitHub Actions, this file is committed back to the repo after each run
so the monitor remembers what it already alerted on.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

EVENTS_URL = "https://en.onepiece-cardgame.com/events/"
STATE_FILE = Path(__file__).parent / "seen.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# Which sections to monitor. Comma-separated, case-insensitive. Defaults to
# all four. Set the INCLUDED_SECTIONS env var (e.g. in the GitHub Actions
# workflow file) to restrict this -- e.g. "Championship,Official Events,Shop Events"
# to exclude Convention Events, or back to the full list to re-include them.
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

# The four section anchors on the page, used only for nicer Discord labeling.
SECTION_ANCHORS = ["CHAMPIONSHIP", "OFFICIALEVENTS", "SHOPEVENTS", "CONVENTIONEVENTS"]


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


def find_section_title(anchor_id: str | None) -> str:
    mapping = {
        "CHAMPIONSHIP": "Championship",
        "OFFICIALEVENTS": "Official Events",
        "SHOPEVENTS": "Shop Events",
        "CONVENTIONEVENTS": "Convention Events",
    }
    return mapping.get(anchor_id, "Events")


def parse_events(html: str):
    """
    Parse every event card/link on the events page.
    Each event link's visible text follows the pattern:
        <Label(s)> <Title> Event Period: <period> Regulation: <reg> <description...>
    e.g. "Tournament Treasure Cup August 2026 Event Period: August 2026 onwards
          Regulation: Standard The ONE PIECE CARD GAME Treasure Cup is a year-round..."
    We split out the label (everything before the title) using known label keywords,
    and flag it as a tournament if "Tournament" appears in that label segment.
    """
    soup = BeautifulSoup(html, "html.parser")
    events = []
    seen_hrefs_this_parse = set()

    # Determine current section by walking the DOM in order and tracking
    # the nearest preceding heading/anchor.
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
            # Only interested in actual event detail pages under /events/
            if "/events/" not in href:
                continue
            full_url = href if href.startswith("http") else f"https://en.onepiece-cardgame.com{href}"
            full_url = full_url.split("#")[0]

            text = clean_text(el.get_text())
            if not text or len(text) < 5:
                continue
            # Skip nav links like "FIND AN EVENT", "LEARN MORE", "VIEW ALL EVENTS"
            if full_url.rstrip("/").endswith("/events"):
                continue
            if text.upper() in {"LEARN MORE", "FIND AN EVENT", "FIND RELATED EVENTS", "VIEW ALL EVENTS", "PAST EVENTS"}:
                continue
            # Must contain "Event Period:" to be a real event card
            if "Event Period:" not in text:
                continue
            if full_url in seen_hrefs_this_parse:
                continue
            seen_hrefs_this_parse.add(full_url)

            # Split off the label (text before the title) -- label ends right
            # before "Event Period:" only tells us where the period starts,
            # but the title itself sits between the label and "Event Period:".
            # Known label keywords used on this site:
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

            # Title is everything up to "Event Period:"
            period_idx = rest.find("Event Period:")
            title = rest[:period_idx].strip() if period_idx != -1 else rest.strip()

            # Extract period string
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


def send_discord_notification(event: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("WARNING: DISCORD_WEBHOOK_URL not set, skipping Discord notification.")
        print(f"Would have notified about: {event['title']} ({event['url']})")
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


def main():
    print(f"Fetching {EVENTS_URL} ...")
    html = fetch_page(EVENTS_URL)

    all_events = parse_events(html)
    in_scope = [e for e in all_events if e["section"].lower() in INCLUDED_SECTIONS]
    tournaments = [e for e in in_scope if e["is_tournament"]]

    excluded_count = len(all_events) - len(in_scope)
    print(f"Monitoring sections: {', '.join(s for s in ALL_SECTIONS if s.lower() in INCLUDED_SECTIONS)}")
    if excluded_count:
        print(f"({excluded_count} event(s) skipped -- outside monitored sections)")
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

    # Also record any non-tournament events we've seen so we don't
    # re-evaluate them every run (keeps state file meaningful), but only
    # tournament URLs gate notifications. Only in-scope events are recorded,
    # so excluded sections won't be marked "seen" until you re-include them.
    for e in in_scope:
        seen.add(e["url"])

    save_seen(seen)
    print("Done.")


if __name__ == "__main__":
    main()
