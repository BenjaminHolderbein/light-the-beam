# 💜 light-the-beam

**Your own beam, at home.** When the Kings win, the purple beam goes up over Golden 1 Center.
This makes a smart bulb in your house go Kings purple too, within about a minute of the final
buzzer, home or away.

It runs on any always-on computer (Raspberry Pi, home server, old laptop), watches the Kings'
games through ESPN's public scoreboard, and tells Alexa to turn your bulb purple the moment the
Kings win. If your bulbs work with Alexa, you can set it up without a smart-home hub or a
developer account.

```
ESPN scoreboard ──(every 15s during the game)──▶ light-the-beam
                                                    │  Kings ahead at the buzzer?
                                                    ▼
                         Virtual Smart Home URL trigger ("doorbell" in Alexa)
                                                    │
                                                    ▼
                         Alexa Routine: set bulb → purple 💜
```

## How it works

- **Between games it sleeps.** It checks the Kings' schedule and wakes up 5 minutes before
  tipoff.
- **During games it watches the scoreboard every 15 seconds**, and fires the moment the 4th
  quarter (or an overtime) ends with the Kings ahead. A game can't end tied, so that's a win,
  and it usually beats ESPN marking the game "Final". "Final" is the fallback.
- **Alexa does the lighting.** Alexa has no public API for controlling lights, so this uses the
  free [Virtual Smart Home](https://www.virtualsmarthome.xyz) skill. It gives you a secret URL
  that shows up in Alexa as a doorbell, and an Alexa Routine turns "doorbell pressed" into
  "bulb goes purple". That means any bulb Alexa can control works, whatever the brand or
  however it's connected (Hue without a bridge, Kasa, Govee, Wiz…).
- **Each win fires once**, even if the computer restarts. If it was down at the buzzer, it
  still lights the beam when it comes back, as long as tipoff was less than 3½ hours ago.
- **Optional phone alerts** through [ntfy](https://ntfy.sh): "Kings beat SA 112–104 · lit 18s
  after the buzzer".

## What you need

- A smart bulb that can change color and that you control with Alexa
- The Alexa app, and the Amazon account your Echo/bulb uses
- An always-on computer running Linux (or macOS) with **Python 3.11+**. There are no
  third-party packages; [uv](https://docs.astral.sh/uv/) is optional
- About 15 minutes

## Setup

### 1. Create the trigger (virtualsmarthome.xyz)

1. Go to **[virtualsmarthome.xyz](https://www.virtualsmarthome.xyz)** and sign in with the
   **same Amazon account** your bulb is on.
2. Open **URL Routine Trigger** and create a trigger named **Light the Beam**.
3. Copy the **JSON response** URL (the one ending in `&response=json`). This is your
   `TRIGGER_URL`. **Treat it like a password**: anyone who has it can fire your routine.

### 2. Wire it to your bulb (Alexa app)

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

### 3. Install and test

```bash
git clone https://github.com/BenjaminHolderbein/light-the-beam.git
cd light-the-beam
cp .env.example .env        # paste your TRIGGER_URL; optionally set NTFY_TOPIC
```

Fire the beam once to check that everything is wired up:

```bash
uv run python -m src.beam --test-trigger      # or: python3 -m src.beam --test-trigger
```

You should see `(True, 'success')`, and the bulb should turn purple. If you get `success` but
the bulb doesn't change, the problem is in the Alexa Routine (step 2). Check that its
**When** is the Light the Beam device.

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
| `python -m src.beam --check <game id>` | Dry run: show the status and verdict for one game. Fires nothing |
| `python -m unittest` | Run the tests |

The game id is the number in an ESPN game URL, e.g. `espn.com/nba/game/_/gameId/401909861`.
To see the full flow before a Kings win comes along, set `TEAM=LAL` (or any ESPN team
abbreviation) in `.env` and run `--check` on one of that team's recent wins.

## Good to know

- **Speed.** Most of the delay is how fast ESPN's scoreboard registers the buzzer, plus up to
  15 seconds until the next check. The Alexa hop takes a few seconds. Your win notification
  shows the real number.
- **The rare false alarm.** If the Kings are up when the clock hits 0:00 and the other team
  then wins it on free throws after the buzzer, the beam will already have lit. Sorry. Blame
  the refs.
- **Unofficial data.** ESPN's scoreboard API is public but undocumented, and it could change.
  If it breaks, you'll get the "can't reach ESPN" alert instead of silence.
- **Third-party trigger.** Virtual Smart Home is a free community service, not Amazon. If it
  goes down, the trigger fails and you get the "trigger FAILED" alert.
- **Playoffs and the play-in** are included. The schedule covers the regular season,
  play-in and playoffs.

## Files

- `src/beam.py`: the whole thing, Python standard library only
- `.env`: your settings and secrets (git-ignored, copied from `.env.example`)
- `state.json`: games already handled, so nothing fires twice (git-ignored)
- `light-the-beam.service`: systemd unit template

---

Not affiliated with the Sacramento Kings, the NBA, ESPN, or Amazon. Just a fan who wanted the
beam at home. MIT licensed. **Light the beam.** 💜
