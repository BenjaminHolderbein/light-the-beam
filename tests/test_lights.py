import unittest
from unittest import mock

from src import lights

# What a bridge with one room, one zone and three bulbs answers with. Trimmed
# to the fields the code reads, with the shapes kept exactly as CLIP v2 sends
# them: everything wrapped in {"errors": [], "data": [...]}.
ROOMS = {"errors": [], "data": [{
    "id": "room-1",
    "metadata": {"name": "Living room"},
    "services": [{"rid": "group-1", "rtype": "grouped_light"},
                 {"rid": "dev-9", "rtype": "device"}],
}]}
ZONES = {"errors": [], "data": [{
    "id": "zone-1",
    "metadata": {"name": "Downstairs"},
    "services": [{"rid": "group-2", "rtype": "grouped_light"}],
}]}
LIGHTS = {"errors": [], "data": [
    {"id": "light-1", "metadata": {"name": "Couch lamp"}, "color": {"xy": {"x": 0.4, "y": 0.4}}},
    {"id": "light-2", "metadata": {"name": "TV strip"}, "color": {"xy": {"x": 0.2, "y": 0.2}}},
    {"id": "light-3", "metadata": {"name": "Hallway"}},  # no colour support
]}


def fake_bridge(puts=None, fail=()):
    """A stand-in for lights.request. Records PUTs into `puts`."""
    def request(ip, method, path, body=None, key=None, timeout=10):
        if method == "PUT":
            if path.rsplit("/", 1)[-1] in fail:
                raise lights.LightError("bulb unreachable")
            if puts is not None:
                puts.append((path, body))
            return {"errors": [], "data": [{"rid": path.rsplit("/", 1)[-1]}]}, "fp"
        if path.endswith("/room"):
            return ROOMS, "fp"
        if path.endswith("/zone"):
            return ZONES, "fp"
        if path.endswith("/light"):
            return LIGHTS, "fp"
        # A single-resource GET, used when capturing state to restore.
        rid = path.rsplit("/", 1)[-1]
        for item in LIGHTS["data"] + [{"id": "group-1", "on": {"on": False},
                                       "dimming": {"brightness": 42.0}}]:
            if item["id"] == rid:
                return {"errors": [], "data": [item]}, "fp"
        return {"errors": [], "data": []}, "fp"
    return request


def env(**kw):
    base = {"LIGHT_BACKEND": "hue", "HUE_BRIDGE": "10.0.0.5", "HUE_KEY": "k",
            "HUE_TARGETS": "Living room", "HUE_CERT_SHA256": "ab"}
    base.update(kw)
    return mock.patch.dict("os.environ", base, clear=True)


class Colour(unittest.TestCase):
    def test_white_lands_on_the_d65_point(self):
        # The conversion is only right if pure white comes out at D65, which
        # is the one value in the whole space with a published answer.
        self.assertEqual(lights.hex_to_xy("#FFFFFF"), (0.3127, 0.329))

    def test_kings_purple_is_in_the_violet_corner(self):
        x, y = lights.hex_to_xy(lights.KINGS_PURPLE)
        self.assertTrue(0.25 < x < 0.33, x)
        self.assertTrue(0.08 < y < 0.17, y)

    def test_hash_is_optional_and_case_does_not_matter(self):
        self.assertEqual(lights.hex_to_xy("5a2d81"), lights.hex_to_xy("#5A2D81"))

    def test_junk_colour_is_rejected_by_name(self):
        for bad in ("purple", "#5A2D8", "", "#GGGGGG"):
            with self.assertRaises(lights.LightError):
                lights.hex_to_xy(bad)


class Targets(unittest.TestCase):
    def setUp(self):
        with mock.patch.object(lights, "request", fake_bridge()):
            self.inv = lights.inventory("10.0.0.5", "k")

    def test_inventory_reads_rooms_zones_and_lights(self):
        self.assertEqual(self.inv["groups"],
                         {"Living room": "group-1", "Downstairs": "group-2"})
        self.assertEqual(set(self.inv["lights"]), {"Couch lamp", "TV strip", "Hallway"})
        self.assertFalse(self.inv["colour"]["Hallway"])

    def test_a_room_resolves_to_its_grouped_light(self):
        # Not to its member bulbs: one call makes the whole room change at
        # once, which is the point of a beam.
        self.assertEqual(lights.resolve(["Living room"], self.inv),
                         [("grouped_light", "group-1")])

    def test_a_bulb_resolves_to_itself(self):
        self.assertEqual(lights.resolve(["TV strip"], self.inv), [("light", "light-2")])

    def test_an_unknown_name_lists_what_does_exist(self):
        with self.assertRaises(lights.LightError) as e:
            lights.resolve(["Bedroom"], self.inv)
        self.assertIn("Bedroom", str(e.exception))
        self.assertIn("Living room", str(e.exception))

    def test_a_colourless_bulb_is_kept_but_warned_about(self):
        with self.assertLogs("beam.lights", level="WARNING") as logged:
            got = lights.resolve(["Hallway"], self.inv)
        self.assertEqual(got, [("light", "light-3")])
        self.assertIn("Hallway", logged.output[0])


class Firing(unittest.TestCase):
    def test_it_puts_purple_to_the_resolved_target(self):
        puts = []
        with env(), mock.patch.object(lights, "request", fake_bridge(puts)):
            ok, detail = lights.fire_hue("test")
        self.assertTrue(ok, detail)
        self.assertEqual(len(puts), 1)
        path, body = puts[0]
        self.assertEqual(path, "/clip/v2/resource/grouped_light/group-1")
        self.assertTrue(body["on"]["on"])
        self.assertEqual(body["color"]["xy"], dict(zip("xy", lights.hex_to_xy(lights.KINGS_PURPLE))))

    def test_colour_and_brightness_come_from_the_environment(self):
        puts = []
        with env(HUE_COLOR="#FF0000", HUE_BRIGHTNESS="40"), \
                mock.patch.object(lights, "request", fake_bridge(puts)):
            lights.fire_hue("test")
        self.assertEqual(puts[0][1]["dimming"]["brightness"], 40.0)
        self.assertEqual(puts[0][1]["color"]["xy"]["x"], 0.735)

    def test_brightness_is_clamped_to_what_the_api_accepts(self):
        puts = []
        with env(HUE_BRIGHTNESS="900"), mock.patch.object(lights, "request", fake_bridge(puts)):
            lights.fire_hue("test")
        self.assertEqual(puts[0][1]["dimming"]["brightness"], 100.0)

    def test_several_targets_each_get_a_call(self):
        puts = []
        with env(HUE_TARGETS="Living room, TV strip"), \
                mock.patch.object(lights, "request", fake_bridge(puts)):
            ok, _ = lights.fire_hue("test")
        self.assertTrue(ok)
        self.assertEqual([p for p, _ in puts],
                         ["/clip/v2/resource/grouped_light/group-1",
                          "/clip/v2/resource/light/light-2"])

    def test_one_dead_bulb_does_not_lose_the_others(self):
        # Half a beam beats none, so this reports success and names the
        # failure rather than returning a flat error.
        puts = []
        with env(HUE_TARGETS="Couch lamp, TV strip"), \
                mock.patch.object(lights, "request", fake_bridge(puts, fail={"light-1"})):
            ok, detail = lights.fire_hue("test")
        self.assertTrue(ok)
        self.assertEqual(len(puts), 1)
        self.assertIn("1 failed", detail)

    def test_every_target_failing_is_a_failure(self):
        with env(HUE_TARGETS="Couch lamp"), \
                mock.patch.object(lights, "request", fake_bridge([], fail={"light-1"})):
            ok, detail = lights.fire_hue("test")
        self.assertFalse(ok)
        self.assertIn("unreachable", detail)

    def test_a_bad_target_name_fails_without_touching_a_bulb(self):
        puts = []
        with env(HUE_TARGETS="Garage"), mock.patch.object(lights, "request", fake_bridge(puts)):
            ok, detail = lights.fire_hue("test")
        self.assertFalse(ok)
        self.assertEqual(puts, [])
        self.assertIn("Garage", detail)

    def test_restore_puts_the_old_state_back(self):
        puts = []
        with env(HUE_RESTORE_AFTER="0.05"), \
                mock.patch.object(lights, "request", fake_bridge(puts)):
            ok, _ = lights.fire_hue("test")
            self.assertTrue(ok)
            for _ in range(100):
                if len(puts) > 1:
                    break
                __import__("time").sleep(0.01)
        self.assertEqual(len(puts), 2, "the restore timer never fired")
        self.assertFalse(puts[1][1]["on"]["on"])  # group-1 was off beforehand
        self.assertEqual(puts[1][1]["dimming"]["brightness"], 42.0)

    def test_nothing_is_restored_when_restore_is_off(self):
        puts = []
        with env(), mock.patch.object(lights, "request", fake_bridge(puts)):
            lights.fire_hue("test")
            __import__("time").sleep(0.05)
        self.assertEqual(len(puts), 1)


class Config(unittest.TestCase):
    def test_hue_config_is_checked_before_the_season_starts(self):
        for missing in ("HUE_BRIDGE", "HUE_KEY", "HUE_TARGETS"):
            with env(**{missing: ""}), self.assertRaises(lights.LightError) as e:
                lights.check_config()
            self.assertIn(missing, str(e.exception))

    def test_a_bad_colour_is_caught_at_startup_not_at_the_buzzer(self):
        with env(HUE_COLOR="mauve"), self.assertRaises(lights.LightError):
            lights.check_config()

    def test_an_unpinned_certificate_warns_but_still_runs(self):
        with env(HUE_CERT_SHA256=""), self.assertLogs("beam.lights", level="WARNING"):
            self.assertIn("hue", lights.check_config())

    def test_webhook_needs_its_url(self):
        with mock.patch.dict("os.environ", {"LIGHT_BACKEND": "webhook"}, clear=True), \
                self.assertRaises(lights.LightError):
            lights.check_config()

    def test_backend_is_inferred_from_whichever_config_exists(self):
        with mock.patch.dict("os.environ", {"HUE_BRIDGE": "10.0.0.5"}, clear=True):
            self.assertEqual(lights.backend_name(), "hue")
        with mock.patch.dict("os.environ", {"TRIGGER_URL": "https://x"}, clear=True):
            self.assertEqual(lights.backend_name(), "webhook")
        # An existing install that later adds a bridge keeps working as it was
        # until it says otherwise, rather than silently switching.
        with mock.patch.dict("os.environ",
                             {"TRIGGER_URL": "https://x", "HUE_BRIDGE": "10.0.0.5"}, clear=True):
            self.assertEqual(lights.backend_name(), "webhook")

    def test_an_unknown_backend_is_rejected_by_name(self):
        with mock.patch.dict("os.environ", {"LIGHT_BACKEND": "lifx"}, clear=True), \
                self.assertRaises(lights.LightError) as e:
            lights.backend_name()
        self.assertIn("lifx", str(e.exception))


class Pairing(unittest.TestCase):
    def test_it_waits_for_the_link_button_then_returns_the_key(self):
        replies = [
            ([{"error": {"type": 101, "description": "link button not pressed"}}], "fp"),
            ([{"error": {"type": 101, "description": "link button not pressed"}}], "fp"),
            ([{"success": {"username": "abc123", "clientkey": "def"}}], "fp"),
        ]
        with mock.patch.object(lights, "request", side_effect=replies):
            key, fingerprint = lights.pair("10.0.0.5", sleep=lambda _: None, notify=lambda _: None)
        self.assertEqual((key, fingerprint), ("abc123", "fp"))

    def test_it_gives_up_with_an_instruction_not_a_stack_trace(self):
        reply = ([{"error": {"type": 101, "description": "link button not pressed"}}], "fp")
        with mock.patch.object(lights, "request", return_value=reply), \
                self.assertRaises(lights.LightError) as e:
            lights.pair("10.0.0.5", press_window=-1, sleep=lambda _: None, notify=lambda _: None)
        self.assertIn("link button", str(e.exception))

    def test_an_error_that_is_not_the_button_stops_immediately(self):
        reply = ([{"error": {"type": 6, "description": "parameter not available"}}], "fp")
        with mock.patch.object(lights, "request", return_value=reply) as req, \
                self.assertRaises(lights.LightError):
            lights.pair("10.0.0.5", sleep=lambda _: None, notify=lambda _: None)
        self.assertEqual(req.call_count, 1)


if __name__ == "__main__":
    unittest.main()
