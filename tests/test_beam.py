import unittest

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
