# 💜 light-the-beam

**Your own beam, at home.** When the Kings win, the purple beam goes up over Golden 1 Center.
This makes a smart bulb in your house go Kings purple too, within about a minute of the final
buzzer, home or away.

It runs on any always-on computer (Raspberry Pi, home server, old laptop), watches the Kings'
games through ESPN's public scoreboard, and turns your bulb purple the moment the Kings win.

Two ways to reach the bulbs. Pick the first one that describes you:

```
ESPN scoreboard ──(every 15s during the game)──▶ light-the-beam
                                                    │  Kings ahead at the buzzer?
                      ┌─────────────────────────────┴───────────────────────┐
                      ▼                                                     ▼
       bulbs on a Philips Hue bridge                      any bulb Alexa can control
       Hue CLIP v2 API, on your network                   Virtual Smart Home URL trigger
                      │                                                     │
                      ▼                                                     ▼
          bulbs → purple 💜 directly                      Alexa Routine: set bulb → purple 💜
```

**Hue** is the better path when you have a bridge: no Amazon account, no routine to wire up,
and the colour is set here rather than asked for. **Alexa** works with any brand and needs no
hub, which is why it's still here.

## How it works

- **Between games it sleeps.** It checks the Kings' schedule and wakes up 5 minutes before
  tipoff.
- **During games it watches the scoreboard every 15 seconds**, and fires the moment the 4th
  quarter (or an overtime) ends with the Kings ahead. A game can't end tied, so that's a win,
  and it usually beats ESPN marking the game "Final". "Final" is the fallback.
- **On the Hue path, it sets the colour itself.** The bridge has a local API, so this converts
  Kings purple to the CIE xy value Hue wants and sends it straight to the room. A room or zone
  changes all at once in one call, instead of bulbs lighting one after another.
- **On the Alexa path, Alexa does the lighting.** Alexa has no public API for controlling
  lights, so this uses the free [Virtual Smart Home](https://www.virtualsmarthome.xyz) skill.
  It gives you a secret URL that shows up in Alexa as a doorbell, and an Alexa Routine turns
  "doorbell pressed" into "bulb goes purple". Any bulb Alexa can control works, whatever the
  brand or however it's connected (Kasa, Govee, Wiz…).
- **Each win fires once**, even if the computer restarts. If it was down at the buzzer, it
  still lights the beam when it comes back, as long as tipoff was less than 3½ hours ago.
- **Optional phone alerts** through [ntfy](https://ntfy.sh): "Kings beat SA 112–104 · lit 18s
  after the buzzer".

## What you need

- A smart bulb that can change colour, on **either** a Philips Hue bridge **or** Alexa
- An always-on computer running Linux (or macOS) with **Python 3.11+**, on the same network as
  the bridge if you're using Hue. There are no third-party packages;
  [uv](https://docs.astral.sh/uv/) is optional
- For the Alexa path only: the Alexa app and the Amazon account your Echo/bulb uses
- About 5 minutes for Hue, 15 for Alexa

## Setup

### 1. Install

```bash
git clone https://github.com/BenjaminHolderbein/light-the-beam.git
cd light-the-beam
cp .env.example .env
```

### 2a. Hue: pair with the bridge

Find the bridge's IP in the Hue app: **Settings → My Hue System → Bridge**. Then:

```bash
uv run python -m src.beam --pair-hue 192.168.1.42    # your bridge's IP
```

Press the round button on top of the bridge when it asks. It writes the application key and
the bridge's certificate fingerprint to `.env`, then lists what the bridge can see:

```
Rooms and zones (one call lights the whole thing at once):
  Living room
  Downstairs

Individual lights:
  Couch lamp
  TV strip
  Hallway   (no colour, dims only)
```

Put the ones you want in `.env`, named exactly as the Hue app names them:

```
HUE_TARGETS=Living room
```

A room or zone is better than listing its bulbs: the bridge changes them simultaneously, where
bulb-by-bulb visibly ripples. Re-run `--hue-lights` any time to see the list again.

That's the whole Hue setup. Skip to step 3.

### 2b. Alexa: create the trigger and the routine

Use this if your bulbs aren't on a Hue bridge.

**Create the trigger** at **[virtualsmarthome.xyz](https://www.virtualsmarthome.xyz)**:

1. Sign in with the **same Amazon account** your bulb is on.
2. Open **URL Routine Trigger** and create a trigger named **Light the Beam**.
3. Copy the **JSON response** URL (the one ending in `&response=json`) into `.env` as
   `TRIGGER_URL`. **Treat it like a password**: anyone who has it can fire your routine.

**Wire it to your bulb** in the Alexa app:

1. **More → Skills & Games**, search **Virtual Smart Home**, tap **Enable to Use**, and link
   your account.
2. Let Alexa discover devices. **Light the Beam** appears as a doorbell.
3. **More → Routines → +**
   - **When this happens:** Smart Home → **Light the Beam**
   - **Action:** Smart Home → your bulb → **Color: purple**, **Brightness: 100%**
   - *(Optional)* add **Wait** (e.g. 1 hour) and then turn the bulb off, or set it back to your
     usual color.
4. **Silence the doorbell.** If you skip this, your Echo announces *"Someone is at Light the
   Beam"* every time it fires. Go to **Devices → Light the Beam → ⚙️** and turn off
   **Doorbell Press Announcements** (and Notifications). The Routine still runs.

### 3. Test

Fire the beam once to check that everything is wired up:

```bash
uv run python -m src.beam --test-trigger      # or: python3 -m src.beam --test-trigger
```

The bulb should turn purple, and you should see `(True, '[hue] 1 target(s) purple · 0.1s')` or
`(True, '[webhook] success · 0.4s')`.

If it fails, the message says which backend it tried and why it didn't work: a name that isn't
on the bridge is listed against the names that are, and a rejected key tells you to pair again.
On the Alexa path, `success` with a bulb that doesn't change means the problem is in the Routine
(step 2b) — check that its **When** is the Light the Beam device.

### 4. (Optional) Phone notifications

Install the [ntfy app](https://ntfy.sh), subscribe to a topic name nobody would guess (e.g.
`beam-<random letters>`), and put the same name in `.env` as `NTFY_TOPIC`. You'll get:

| Notification | When |
|---|---|
| 💜 **Light the beam!** — "Kings beat SA 112–104 · lit 18s after the buzzer" | every win |
| ⚠️ **Beam trigger FAILED** | the trigger URL didn't respond with `success` |
| ⚠️ **can't reach ESPN** | ~20 failed checks in a row (about 5 minutes during a game) |
| 📡 **beam watcher alive** (silent) | once a day, with the next game's tipoff |
| 🔍 **beam debug** (low priority) | only with `NTFY_DEBUG=1`: each trigger fire, live-watch start, each game's result |

The "lit Ns after the buzzer" figure is measured against the real-world time of the final
play in ESPN's play-by-play data, so you can see how quickly your beam actually lit.

### 5. Run it 24/7 (systemd)

On a Linux box with systemd, the included unit keeps it running and restarts it after reboots:

```bash
sed -e "s|YOUR_USER|$USER|" \
    -e "s|/path/to/light-the-beam|$PWD|g" \
    -e "s|/path/to/uv|$(command -v uv)|" \
    light-the-beam.service | sudo tee /etc/systemd/system/light-the-beam.service
sudo systemctl daemon-reload
sudo systemctl enable --now light-the-beam
journalctl -u light-the-beam -f          # logs
```

No uv? Replace the `ExecStart=` line with
`ExecStart=/usr/bin/python3 -m src.beam`.

Not using systemd? `python3 -m src.beam` in `tmux`/`screen`, or as a launchd agent on a Mac,
works fine. It's a single long-running process.

## Commands

| Command | What it does |
|---|---|
| `python -m src.beam` | Run the watcher (what the service runs). Add `-v` for every scoreboard check |
| `python -m src.beam --test-trigger` | Light the beam right now |
| `python -m src.beam --pair-hue <ip>` | One-time Hue setup: press the bridge's link button, this writes the key to `.env` |
| `python -m src.beam --hue-lights` | List the bridge's rooms, zones and lights, with the `HUE_TARGETS` line to paste |
| `python -m src.beam --check <game id>` | Dry run: show the status and verdict for one game. Fires nothing |
| `python -m unittest` | Run the tests |

The game id is the number in an ESPN game URL, e.g. `espn.com/nba/game/_/gameId/401909861`.
To see the full flow before a Kings win comes along, set `TEAM=LAL` (or any ESPN team
abbreviation) in `.env` and run `--check` on one of that team's recent wins.

## Good to know

- **Speed.** Most of the delay is how fast ESPN's scoreboard registers the buzzer, plus up to
  15 seconds until the next check. Hue adds about a tenth of a second; the Alexa hop takes a
  few seconds. Your win notification shows the real number.
- **The rare false alarm.** If the Kings are up when the clock hits 0:00 and the other team
  then wins it on free throws after the buzzer, the beam will already have lit. Sorry. Blame
  the refs.
- **Unofficial data.** ESPN's scoreboard API is public but undocumented, and it could change.
  If it breaks, you'll get the "can't reach ESPN" alert instead of silence.
- **Third-party trigger.** Virtual Smart Home is a free community service, not Amazon. If it
  goes down, the trigger fails and you get the "trigger FAILED" alert. The Hue path has no
  third party in it and keeps working with the internet down, as long as ESPN was reachable
  when the game ended.
- **The bridge's certificate.** The bridge signs its own, so it can't be checked against the
  normal authorities, and the name on it is the bridge id rather than its IP. Pairing records
  the fingerprint in `HUE_CERT_SHA256` and every later call refuses anything else, which is
  what stops another device on your network answering as the bridge. If you replace or factory
  reset the bridge, pair again.
- **One dead bulb isn't a dead beam.** With several targets, the ones that answer go purple and
  the notification names the ones that didn't.
- **Playoffs and the play-in** are included. The schedule covers the regular season,
  play-in and playoffs.

## Files

- `src/beam.py`: the watcher. Schedule, scoreboard, verdict, state
- `src/lights.py`: everything that touches a bulb, both backends. Standard library only
- `.env`: your settings and secrets (git-ignored, copied from `.env.example`)
- `state.json`: games already handled, so nothing fires twice (git-ignored)
- `light-the-beam.service`: systemd unit template

---

Not affiliated with the Sacramento Kings, the NBA, ESPN, or Amazon. Just a fan who wanted the
beam at home. MIT licensed. **Light the beam.** 💜
