"""Light the beam: fire an Alexa routine (via Virtual Smart Home) within ~a minute of a Kings win.

Idle between games, poll ESPN's scoreboard every 15s from just before tipoff, and fire the
trigger the moment the 4th quarter (or an OT) ends with the Kings ahead -- a game can't end
tied, so that's the win, usually before ESPN flips the game to "Final". Final is the fallback.
"""

import argparse
import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state.json"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ET = ZoneInfo("America/New_York")
SEASON_TYPES = (2, 3, 5)  # regular season, playoffs, play-in

LIVE_POLL = 15  # seconds between scoreboard checks during a game
IDLE_POLL_MAX = 600  # re-check the schedule at least this often between games
PREGAME_LEAD = timedelta(minutes=5)
CATCHUP_WINDOW = timedelta(hours=3, minutes=30)  # after tipoff; covers a reboot mid-game
FAIL_ALERT_AFTER = 20  # consecutive failed fetches (~5 min while live) before a loud alert
HEARTBEAT_EVERY = timedelta(days=1)

log = logging.getLogger("beam")


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "light-the-beam/0.1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def ntfy(msg, title=None, priority="default", tags="purple_heart"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        log.warning("no NTFY_TOPIC; would have sent: %s", msg)
        return
    headers = {"Priority": priority, "Tags": tags}
    if title:
        headers["Title"] = title
    url = f"{os.environ.get('NTFY_SERVER', 'https://ntfy.sh')}/{topic}"
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=msg.encode(), headers=headers), timeout=10)
    except Exception as e:
        log.error("ntfy failed: %s", e)


def fire_trigger():
    """Hit the Virtual Smart Home URL. Returns (ok, detail)."""
    try:
        req = urllib.request.Request(os.environ["TRIGGER_URL"], headers={"User-Agent": "light-the-beam/0.1"})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode(errors="replace")[:300]
            return r.status == 200 and "error" not in body.lower(), body
    except Exception as e:
        return False, str(e)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return {"handled": {}}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def schedule(team):
    """All of the team's games this season: [(event_id, start_utc, short_name)]."""
    games = []
    for st in SEASON_TYPES:
        try:
            events = get_json(f"{ESPN}/teams/{team}/schedule?seasontype={st}").get("events", [])
        except Exception as e:
            if st == 2:
                raise
            log.debug("seasontype %s unavailable: %s", st, e)
            continue
        games += [(e["id"], parse_time(e["date"]), e["shortName"]) for e in events]
    return sorted(games, key=lambda g: g[1])


def game_status(event_id, start, team):
    """Live status for one game from the scoreboard (ESPN keys scoreboard dates in ET)."""
    day = start.astimezone(ET).strftime("%Y%m%d")
    for e in get_json(f"{ESPN}/scoreboard?dates={day}")["events"]:
        if e["id"] == event_id:
            return parse_status(e, team)
    raise LookupError(f"event {event_id} not on scoreboard for {day}")


def parse_status(event, team):
    comp = event["competitions"][0]
    st = comp["status"]
    us = next(c for c in comp["competitors"] if c["team"]["abbreviation"] == team)
    them = next(c for c in comp["competitors"] if c is not us)
    return {
        "name": event["shortName"],
        "state": st["type"]["state"],  # pre / in / post
        "completed": st["type"]["completed"],
        "detail": st["type"]["name"],
        "period": st.get("period", 0),
        "clock": st.get("clock", 0.0),
        "us": int(us.get("score") or 0),
        "them": int(them.get("score") or 0),
        "opp": them["team"]["abbreviation"],
        "us_won": bool(us.get("winner")),
    }


def verdict(g):
    """'win' / 'loss' / 'over' (postponed etc.) / None (still going)."""
    if g["state"] == "post":
        if not g["completed"]:
            return "over"
        return "win" if g["us_won"] else "loss"
    if g["state"] == "in" and g["period"] >= 4 and g["clock"] == 0 and g["us"] > g["them"]:
        return "win"  # buzzer: regulation/OT ended with us ahead
    return None


def buzzer_wallclock(event_id):
    """Real-world time of ESPN's last 'End' play, for measuring buzzer→beam lag. Best-effort."""
    try:
        plays = get_json(f"{ESPN}/summary?event={event_id}").get("plays", [])
        ends = [p for p in plays if p.get("wallclock") and p["type"]["text"] in ("End Game", "End Period")]
        return parse_time(ends[-1]["wallclock"]) if ends else None
    except Exception:
        return None


def light_the_beam(event_id, g, late=False):
    fired_at = datetime.now(timezone.utc)
    ok, detail = fire_trigger()
    score = f"{g['us']}–{g['them']}"
    log.info("fire %s (%s %s): ok=%s %s", event_id, g["name"], score, ok, detail)
    if not ok:
        ntfy(f"Trigger failed after {g['name']} win ({score}): {detail}",
             title="Beam trigger FAILED", priority="high", tags="warning")
        return
    lag = ""
    time.sleep(20)  # give ESPN a moment to post the closing play
    if (buzzer := buzzer_wallclock(event_id)) is not None:
        lag = f" · lit {int((fired_at - buzzer).total_seconds())}s after the buzzer"
    note = " (late — couch was down at the buzzer)" if late else ""
    ntfy(f"Kings beat {g['opp']} {score}{lag}{note}", title="💜 Light the beam!")


def run(team):
    state = load_state()
    fails = 0
    alerted = False
    last_heartbeat = datetime.min.replace(tzinfo=timezone.utc)
    first_pass = True
    while True:
        now = datetime.now(timezone.utc)
        try:
            games = [(i, s, n) for i, s, n in schedule(team)
                     if i not in state["handled"] and s > now - CATCHUP_WINDOW]
            if not games:
                time.sleep(IDLE_POLL_MAX)
                continue
            event_id, start, name = games[0]
            if now - last_heartbeat > HEARTBEAT_EVERY:
                ntfy(f"Watching. Next: {name} {start.astimezone(ZoneInfo('America/Los_Angeles')):%a %b %-d %-I:%M%p} PT",
                     title="beam watcher alive", priority="min", tags="satellite")
                last_heartbeat = now
            if start - now > PREGAME_LEAD:
                wait = min((start - PREGAME_LEAD - now).total_seconds(), IDLE_POLL_MAX)
                log.debug("next %s at %s; sleeping %ds", name, start, wait)
                first_pass = False
                fails = 0
                time.sleep(wait)
                continue
            g = game_status(event_id, start, team)
            fails = 0
            if alerted:
                ntfy("ESPN reachable again.", title="beam watcher recovered", tags="white_check_mark")
                alerted = False
            v = verdict(g)
            if v:
                # A game that was already final when we started is a catch-up after a reboot.
                late = first_pass and g["state"] == "post"
                if v == "win":
                    light_the_beam(event_id, g, late=late)
                else:
                    log.info("%s: %s %d–%d", g["name"], v, g["us"], g["them"])
                state["handled"][event_id] = {"result": v, "at": now.isoformat(), "score": f"{g['us']}-{g['them']}"}
                save_state(state)
                continue
            first_pass = False
            log.debug("%s: %s P%s %.1f %d–%d", g["name"], g["detail"], g["period"], g["clock"], g["us"], g["them"])
            time.sleep(LIVE_POLL)
        except Exception as e:
            fails += 1
            log.warning("fetch failed (%d): %s", fails, e)
            if fails >= FAIL_ALERT_AFTER and not alerted:
                ntfy(f"{fails} failed checks in a row: {e}", title="beam watcher can't reach ESPN",
                     priority="high", tags="warning")
                alerted = True
            time.sleep(LIVE_POLL)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--check", metavar="EVENT_ID", help="dry run: show status/verdict for one game, fire nothing")
    p.add_argument("--test-trigger", action="store_true", help="fire the trigger once now")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    load_env()
    team = os.environ.get("TEAM", "SAC")
    if args.test_trigger:
        print(fire_trigger())
    elif args.check:
        start = next(s for i, s, _ in schedule(team) if i == args.check)
        g = game_status(args.check, start, team)
        print(json.dumps(g, indent=2), "\nverdict:", verdict(g), "\nbuzzer wallclock:", buzzer_wallclock(args.check))
    else:
        run(team)


if __name__ == "__main__":
    main()
