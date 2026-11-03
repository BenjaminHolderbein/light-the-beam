import json
import unittest
from unittest import mock

from src import beam
from src.beam import parse_status, verdict


def event(state="in", completed=False, period=4, clock=0.0, kings=101, opp=99, kings_won=False):
    return {
        "shortName": "SAC @ SA",
        "competitions": [{
            "status": {"clock": clock, "period": period,
                       "type": {"name": "STATUS_END_PERIOD", "state": state, "completed": completed}},
            "competitors": [
                {"homeAway": "home", "score": str(opp), "team": {"abbreviation": "SA"}},
                {"homeAway": "away", "score": str(kings), "winner": kings_won, "team": {"abbreviation": "SAC"}},
            ],
        }],
    }


def v(**kw):
    return verdict(parse_status(event(**kw), "SAC"))


class Verdict(unittest.TestCase):
    def test_kings_ahead_at_the_buzzer_lights_the_beam(self):
        self.assertEqual(v(), "win")

    def test_kings_ahead_at_the_overtime_buzzer_lights_the_beam(self):
        self.assertEqual(v(period=5), "win")

    def test_kings_trailing_at_the_buzzer(self):
        self.assertIsNone(v(kings=95))

    def test_tied_at_the_buzzer_goes_to_overtime(self):
        self.assertIsNone(v(kings=99))

    def test_end_of_the_third_is_not_the_end(self):
        self.assertIsNone(v(period=3))

    def test_lead_with_time_left_is_not_a_win_yet(self):
        self.assertIsNone(v(clock=30.0))

    def test_final_win_and_loss(self):
        self.assertEqual(v(state="post", completed=True, clock=720.0, kings_won=True), "win")
        self.assertEqual(v(state="post", completed=True, clock=720.0, kings=90), "loss")

    def test_postponed(self):
        self.assertEqual(v(state="post"), "over")

    def test_pregame_blank_scores(self):
        e = event(state="pre", period=0)
        for c in e["competitions"][0]["competitors"]:
            c["score"] = ""
        self.assertIsNone(verdict(parse_status(e, "SAC")))


if __name__ == "__main__":
    unittest.main()


class Notifications(unittest.TestCase):
    """The win notification's title starts with 💜, which cannot go in an HTTP
    header. It raised inside ntfy(), got caught and logged, and the Kings' win
    on 2026-11-02 lit the beam and never reached Ben's phone."""

    def send(self, **kw):
        sent = {}

        def urlopen(req, timeout=None):
            sent["url"] = req.full_url
            sent["body"] = json.loads(req.data.decode())
            sent["headers"] = req.headers
            return mock.MagicMock(__enter__=mock.MagicMock(), __exit__=mock.MagicMock())

        with mock.patch.dict("os.environ", {"NTFY_TOPIC": "t"}, clear=True), \
                mock.patch("src.beam.urllib.request.urlopen", urlopen):
            beam.ntfy(**kw)
        return sent

    def test_an_emoji_title_survives(self):
        sent = self.send(msg="Kings beat DAL 115–100", title="💜 Light the beam!")
        self.assertEqual(sent["body"]["title"], "💜 Light the beam!")
        self.assertEqual(sent["body"]["message"], "Kings beat DAL 115–100")

    def test_priority_names_become_numbers(self):
        self.assertEqual(self.send(msg="x", priority="high")["body"]["priority"], 4)
        self.assertEqual(self.send(msg="x", priority="min")["body"]["priority"], 1)
        # An unknown name must not become a crash or a silent urgent.
        self.assertEqual(self.send(msg="x", priority="whatever")["body"]["priority"], 3)

    def test_tags_become_a_list(self):
        self.assertEqual(self.send(msg="x", tags="warning, mag")["body"]["tags"],
                         ["warning", "mag"])

    def test_no_topic_means_no_request(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("src.beam.urllib.request.urlopen") as urlopen, \
                self.assertLogs("beam", level="WARNING"):
            beam.ntfy("nobody is listening")
        urlopen.assert_not_called()
