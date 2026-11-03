"""Where the purple actually happens.

Two backends, picked with LIGHT_BACKEND:

    webhook   the original. One GET to a Virtual Smart Home URL and Alexa does
              the lighting. Works with any bulb Alexa can reach, whatever the
              brand, and needs no hub.
    hue       talk to a Hue bridge on your own network. Use this if your bulbs
              are already on a bridge: no Amazon account, no routine to wire
              up, and the colour is set here rather than asked for politely.

The Hue path is the CLIP v2 API over HTTPS. Not v1, and not plain HTTP: Signify
has said new features are v2-only and v1 goes away eventually, bridge firmware
no longer serves HTTP at all, and UPnP discovery was switched off in 2022. So
v2 over TLS is the only thing worth building on, which means dealing with the
bridge's self-signed certificate (see _connect).

Everything here is stdlib. The project has no third-party dependencies and that
is worth keeping: it runs on whatever old machine is already in the house.
"""

import hashlib
import http.client
import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.request

log = logging.getLogger("beam.lights")

#: Sacramento Kings purple.
KINGS_PURPLE = "#5A2D81"

#: The bridge's own hard limit on how long the link button stays "pressed".
PAIR_WINDOW = 120


class LightError(Exception):
    """Something in the lighting path is wrong. The message is for a human."""


# -- colour ------------------------------------------------------------------
# Hue wants CIE xy, not RGB. The gamma curve and matrix below are the ones
# Signify publishes for the conversion. Out-of-gamut values are not clamped
# here on purpose: the bridge already clamps to the nearest colour the bulb
# can actually make, and it knows each bulb's gamut while this does not.


def _gamma(c):
    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92


def hex_to_xy(value):
    """'#5A2D81' -> (x, y). Accepts with or without the hash, any case."""
    h = value.strip().lstrip("#")
    if len(h) != 6:
        raise LightError(f"colour must be a 6-digit hex like {KINGS_PURPLE}, got {value!r}")
    try:
        r, g, b = (_gamma(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    except ValueError:
        raise LightError(f"colour must be a 6-digit hex like {KINGS_PURPLE}, got {value!r}") from None
    X = r * 0.649926 + g * 0.103455 + b * 0.197109
    Y = r * 0.234327 + g * 0.743075 + b * 0.022598
    Z = g * 0.053077 + b * 1.035763
    total = X + Y + Z
    if total == 0:
        return 0.0, 0.0  # black; the bridge will read this as off-ish
    return round(X / total, 4), round(Y / total, 4)


# -- bridge transport --------------------------------------------------------


def _connect(ip, timeout):
    """Open a TLS connection to the bridge and return (conn, cert fingerprint).

    The bridge presents a certificate signed by Signify's own CA with the
    bridge id as its common name, so neither the system trust store nor
    hostname checking against an IP can validate it. Verification is therefore
    off, and HUE_CERT_SHA256 is how you get it back: pairing records the
    fingerprint and every later call refuses a certificate that doesn't match.
    That is weaker than real PKI but it does stop someone on the LAN
    impersonating the bridge and collecting the application key.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(ip, 443, timeout=timeout, context=ctx)
    try:
        conn.connect()
    except (OSError, socket.timeout) as e:
        raise LightError(f"cannot reach the Hue bridge at {ip}: {e}") from None
    fingerprint = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
    expected = os.environ.get("HUE_CERT_SHA256", "").strip().lower().replace(":", "")
    if expected and expected != fingerprint:
        conn.close()
        raise LightError(
            f"the bridge at {ip} presented a different certificate than at pairing "
            f"(expected {expected[:16]}…, got {fingerprint[:16]}…). Either the bridge was "
            "replaced or reset, in which case re-pair, or something is impersonating it."
        )
    return conn, fingerprint


def request(ip, method, path, body=None, key=None, timeout=10):
    """One call to the bridge. Returns parsed JSON. Patched out in tests."""
    conn, fingerprint = _connect(ip, timeout)
    headers = {"Accept": "application/json", "User-Agent": "light-the-beam/0.2"}
    if key:
        headers["hue-application-key"] = key
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode(errors="replace")
        if resp.status == 403:
            raise LightError(
                "the bridge rejected the application key (403). Re-run --pair-hue."
            )
        if resp.status >= 400:
            raise LightError(f"bridge returned {resp.status} for {path}: {raw[:200]}")
    finally:
        conn.close()
    try:
        return json.loads(raw), fingerprint
    except ValueError:
        raise LightError(f"bridge sent non-JSON for {path}: {raw[:200]}") from None


def _data(payload, path):
    """CLIP v2 wraps everything in {"errors": [...], "data": [...]}."""
    if payload.get("errors"):
        raise LightError(f"bridge reported an error for {path}: {payload['errors']}")
    return payload.get("data") or []


# -- pairing -----------------------------------------------------------------


def pair(ip, press_window=PAIR_WINDOW, sleep=time.sleep, notify=print):
    """Mint an application key. The link button must be pressed first.

    Returns (key, cert fingerprint). Polls for two minutes because that is how
    long the bridge keeps the button "pressed", and because the realistic
    sequence is a person walking to the bridge after starting this.
    """
    devicetype = f"light_the_beam#{socket.gethostname()[:19]}"
    deadline = time.monotonic() + press_window
    waited = False
    while True:
        payload, fingerprint = request(
            ip, "POST", "/api", body={"devicetype": devicetype, "generateclientkey": True}
        )
        entry = payload[0] if isinstance(payload, list) and payload else {}
        if "success" in entry:
            return entry["success"]["username"], fingerprint
        error = entry.get("error") or {}
        # 101 is "link button not pressed". Anything else is not worth waiting out.
        if error.get("type") != 101:
            raise LightError(f"pairing failed: {error.get('description') or payload}")
        if time.monotonic() > deadline:
            raise LightError(
                "the link button was never pressed. Press the round button on top of the "
                "bridge, then run this again within two minutes."
            )
        if not waited:
            notify("Press the round link button on top of the bridge…")
            waited = True
        sleep(2)


# -- targets -----------------------------------------------------------------


def inventory(ip, key):
    """What the bridge has: rooms and zones (as groups) and individual lights.

    Returns {"groups": {name: id}, "lights": {name: id}, "colour": {name: bool}}.
    Groups win when a name is in both, because lighting a room in one call is
    both faster and visually simultaneous, which matters for a beam.
    """
    groups, lights, colour = {}, {}, {}
    for kind in ("room", "zone"):
        for item in _data(request(ip, "GET", f"/clip/v2/resource/{kind}", key=key)[0], kind):
            name = (item.get("metadata") or {}).get("name")
            grouped = next(
                (s["rid"] for s in item.get("services", []) if s.get("rtype") == "grouped_light"),
                None,
            )
            if name and grouped:
                groups[name] = grouped
    for item in _data(request(ip, "GET", "/clip/v2/resource/light", key=key)[0], "light"):
        name = (item.get("metadata") or {}).get("name")
        if name:
            lights[name] = item["id"]
            colour[name] = "color" in item
    return {"groups": groups, "lights": lights, "colour": colour}


def resolve(targets, inv):
    """Names to [(kind, id)]. Raises naming what is actually available."""
    out, missing, mono = [], [], []
    for name in targets:
        if name in inv["groups"]:
            out.append(("grouped_light", inv["groups"][name]))
        elif name in inv["lights"]:
            if not inv["colour"].get(name, True):
                mono.append(name)
            out.append(("light", inv["lights"][name]))
        else:
            missing.append(name)
    if missing:
        known = sorted(set(inv["groups"]) | set(inv["lights"]))
        raise LightError(
            f"HUE_TARGETS names nothing on the bridge: {', '.join(missing)}. "
            f"Available: {', '.join(known) or '(nothing)'}"
        )
    if mono:
        log.warning("not colour-capable, will only brighten: %s", ", ".join(mono))
    return out


# -- firing ------------------------------------------------------------------


def _targets_from_env():
    raw = os.environ.get("HUE_TARGETS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def check_hue_config():
    """Validate at startup, not at the buzzer. Returns a note for the log."""
    if not os.environ.get("HUE_BRIDGE"):
        raise LightError("LIGHT_BACKEND=hue needs HUE_BRIDGE (the bridge's IP address)")
    if not os.environ.get("HUE_KEY"):
        raise LightError("LIGHT_BACKEND=hue needs HUE_KEY. Run: beam --pair-hue <bridge-ip>")
    targets = _targets_from_env()
    if not targets:
        raise LightError(
            "LIGHT_BACKEND=hue needs HUE_TARGETS, a comma-separated list of room or light "
            "names. Run: beam --hue-lights, to see what the bridge has."
        )
    hex_to_xy(os.environ.get("HUE_COLOR", KINGS_PURPLE))
    if not os.environ.get("HUE_CERT_SHA256"):
        log.warning("HUE_CERT_SHA256 is not set, so the bridge's certificate is not checked")
    return f"hue -> {os.environ['HUE_BRIDGE']} {', '.join(targets)}"


def _state_body():
    x, y = hex_to_xy(os.environ.get("HUE_COLOR", KINGS_PURPLE))
    brightness = float(os.environ.get("HUE_BRIGHTNESS", "100"))
    return {
        "on": {"on": True},
        "dimming": {"brightness": max(1.0, min(100.0, brightness))},
        "color": {"xy": {"x": x, "y": y}},
    }


def _capture(ip, key, targets):
    """Current on/brightness/colour per target, for restoring later."""
    saved = []
    for kind, rid in targets:
        try:
            item = _data(request(ip, "GET", f"/clip/v2/resource/{kind}/{rid}", key=key)[0], rid)[0]
        except (LightError, IndexError):
            continue
        body = {"on": {"on": bool((item.get("on") or {}).get("on"))}}
        if (item.get("dimming") or {}).get("brightness") is not None:
            body["dimming"] = {"brightness": item["dimming"]["brightness"]}
        xy = ((item.get("color") or {}).get("xy") or {})
        if "x" in xy and "y" in xy:
            body["color"] = {"xy": {"x": xy["x"], "y": xy["y"]}}
        saved.append((kind, rid, body))
    return saved


def _apply(ip, key, kind, rid, body):
    request(ip, "PUT", f"/clip/v2/resource/{kind}/{rid}", body=body, key=key)


def fire_hue(reason="manual"):
    """Set the targets to Kings purple. Returns (ok, detail)."""
    ip = os.environ["HUE_BRIDGE"]
    key = os.environ["HUE_KEY"]
    restore_after = float(os.environ.get("HUE_RESTORE_AFTER", "0") or 0)
    try:
        targets = resolve(_targets_from_env(), inventory(ip, key))
        saved = _capture(ip, key, targets) if restore_after > 0 else []
        body = _state_body()
        done, failed = [], []
        for kind, rid in targets:
            try:
                _apply(ip, key, kind, rid, body)
                done.append(rid)
            except LightError as e:
                failed.append(f"{rid}: {e}")
        if not done:
            return False, "; ".join(failed) or "no targets"
        if saved:
            # A thread, because the caller is a polling loop that must not
            # block for several minutes. If the process dies first the lights
            # simply stay purple, which is the harmless direction to fail.
            t = threading.Timer(restore_after, _restore, args=(ip, key, saved))
            t.daemon = True
            t.start()
        detail = f"{len(done)} target(s) purple"
        if failed:
            detail += f"; {len(failed)} failed: {'; '.join(failed)}"
        return True, detail
    except LightError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 — a bulb must never take down the watcher
        return False, f"{type(e).__name__}: {e}"


def _restore(ip, key, saved):
    for kind, rid, body in saved:
        try:
            _apply(ip, key, kind, rid, body)
        except Exception as e:  # noqa: BLE001
            log.warning("could not restore %s %s: %s", kind, rid, e)


# -- webhook (the original) --------------------------------------------------


def fire_webhook(reason="manual"):
    """Hit the Virtual Smart Home URL. Returns (ok, detail)."""
    try:
        req = urllib.request.Request(
            os.environ["TRIGGER_URL"], headers={"User-Agent": "light-the-beam/0.2"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode(errors="replace")
        status = json.loads(raw).get("URLRoutineTrigger", {}).get("triggerActivationStatus")
        return status == "success", status or raw[:300]
    except KeyError:
        return False, "TRIGGER_URL is not set"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# -- dispatch ----------------------------------------------------------------

BACKENDS = {"webhook": fire_webhook, "hue": fire_hue}


def backend_name():
    """Explicit if set, otherwise inferred from whichever config is filled in."""
    chosen = os.environ.get("LIGHT_BACKEND", "").strip().lower()
    if chosen:
        if chosen not in BACKENDS:
            raise LightError(
                f"LIGHT_BACKEND={chosen!r} is not one of: {', '.join(sorted(BACKENDS))}"
            )
        return chosen
    if os.environ.get("HUE_BRIDGE") and not os.environ.get("TRIGGER_URL"):
        return "hue"
    return "webhook"


def check_config():
    """Called once at startup so a misconfiguration surfaces now, not at the
    buzzer in April. Returns a one-line description for the log."""
    name = backend_name()
    if name == "hue":
        return check_hue_config()
    if not os.environ.get("TRIGGER_URL"):
        raise LightError("LIGHT_BACKEND=webhook needs TRIGGER_URL")
    return "webhook -> Virtual Smart Home"


def fire(reason="manual"):
    """Light the beam, whichever way this install is wired. (ok, detail)."""
    name = backend_name()
    t0 = time.monotonic()
    ok, detail = BACKENDS[name](reason)
    return ok, f"[{name}] {detail} · {time.monotonic() - t0:.1f}s"
