# light-the-beam

After every Sacramento Kings win, turn a bulb Kings purple — within about a minute of the buzzer.

- **Scores:** ESPN's public API (`cdn.nba.com` 403s from couch). Idle between games; polls the
  scoreboard every 15s from 5 min before tipoff. Fires when the 4th/OT ends with the Kings
  ahead (beats waiting for "Final"); Final is the fallback.
- **Bulb:** the Hue bulbs are paired to an Echo (no bridge, no API), so the script hits a
  [Virtual Smart Home](https://www.virtualsmarthome.xyz) URL Routine Trigger, and an Alexa
  Routine ("Light the Beam") sets the bulb purple.
- **Alerts (ntfy):** a win notification that includes the measured buzzer→beam lag (from ESPN
  play-by-play wallclock), loud alerts on trigger failure or sustained ESPN outages, and a
  silent daily heartbeat.
- **State:** `state.json` records handled game IDs, so each game fires once, even across reboots.
  A game that finished while couch was down still fires if tipoff was <3.5h ago.

```bash
cp .env.example .env               # fill TRIGGER_URL + NTFY_TOPIC
uv run python -m src.beam --check <espn_event_id>   # dry run, fires nothing
uv run python -m src.beam --test-trigger            # turn the bulb purple now
uv run python -m src.beam -v                         # the service
```

Deployed on couch as `light-the-beam.service` (see couch-server `SETUP.md`).
