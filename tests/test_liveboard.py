"""python3 -m unittest tests/test_liveboard.py  (needs no network)"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "production"))
import liveboard as L

NOW = datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)


def contest(a, sa, b, sb, state="in", detail="Q3", start=NOW):
    return {"start": start, "state": state, "detail": detail, "sides": [
        {"names": [a], "score": str(sa), "winner": None, "sets": []},
        {"names": [b], "score": str(sb), "winner": None, "sets": []}]}


def play(game, market, side, league="NBA"):
    return {"game": game, "market": market, "side": side, "league": league, "start": NOW}


class Status(unittest.TestCase):
    G = "Jazz vs. Pelicans"

    def run_status(self, market, side, ja, pe, state="in"):
        p = play(self.G, market, side)
        c = contest("Utah Jazz", ja, "New Orleans Pelicans", pe, state)
        hit = L.match(p, [c])
        self.assertIsNotNone(hit)
        return L.status(p, *hit)

    def test_moneyline(self):
        self.assertEqual(self.run_status(self.G, "Jazz", 50, 44), "Winning by 6")
        self.assertEqual(self.run_status(self.G, "Pelicans", 50, 44), "Losing by 6")
        self.assertEqual(self.run_status(self.G, "Jazz", 50, 50), "Tied")
        self.assertEqual(self.run_status(self.G, "Jazz", 99, 90, "post"), "Won")

    def test_spread_on_named_favourite(self):
        m = "Spread: Pelicans (-5.5)"
        self.assertEqual(self.run_status(m, "Pelicans", 40, 50), "Covering by 4.5")
        self.assertEqual(self.run_status(m, "Pelicans", 40, 42), "Not covering by 3.5")

    def test_spread_flips_for_the_other_team(self):
        m = "Spread: Pelicans (-5.5)"
        self.assertEqual(self.run_status(m, "Jazz", 40, 42), "Covering by 3.5")
        self.assertEqual(self.run_status(m, "Jazz", 40, 50), "Not covering by 4.5")
        self.assertEqual(self.run_status(m, "Jazz", 90, 100, "post"), "Lost")

    def test_total(self):
        m = self.G + ": O/U 222.5"
        self.assertEqual(self.run_status(m, "Over", 60, 60), "Needs 102.5 more (total 120)")
        self.assertEqual(self.run_status(m, "Over", 120, 110), "Over already hit (total 230)")
        self.assertEqual(self.run_status(m, "Under", 120, 110), "Under already lost (total 230)")
        self.assertEqual(self.run_status(m, "Under", 100, 100, "post"), "Won (total 200)")

    def test_prop_and_period_are_not_guessed(self):
        self.assertIn("unavailable", self.run_status("LeBron James: Points O/U 25.5", "Over", 50, 40))
        self.assertIn("unavailable", self.run_status("1H Spread: Pelicans (-2.5)", "Jazz", 50, 40))

    def test_not_started(self):
        self.assertEqual(self.run_status(self.G, "Jazz", 0, 0, "pre"), "Not started")


class Soccer(unittest.TestCase):
    G = "FC Porto vs. SSC Napoli"

    def st(self, market, side, po, na, state="in", detail="60'"):
        p = play(self.G, market, side, "UCL")
        hit = L.match(p, [contest("Porto", po, "Napoli", na, state, detail)])
        self.assertIsNotNone(hit)
        return L.status(p, *hit)

    def test_yes_no(self):
        m = "Will SSC Napoli win on 2026-11-04?"
        self.assertEqual(self.st(m, "Yes", 0, 1), "Winning")
        self.assertEqual(self.st(m, "No", 0, 1), "Losing")
        self.assertEqual(self.st(m, "No", 1, 1), "Winning")
        self.assertEqual(self.st(m, "No", 1, 1, "post", "FT"), "Won")

    def test_draw(self):
        m = "Will FC Porto vs. SSC Napoli end in a draw?"
        self.assertEqual(self.st(m, "Yes", 1, 1), "Winning")
        self.assertEqual(self.st(m, "No", 1, 1), "Losing")

    def test_extra_time_is_not_settled_here(self):
        self.assertIn("settlement", self.st("Will SSC Napoli win on 2026-11-04?", "Yes", 1, 2, "post", "AET"))


class Tennis(unittest.TestCase):
    G = "Rolex Paris Masters: Matteo Berrettini vs Carlos Alcaraz"

    def contest(self, state="in"):
        return {"start": NOW, "state": state, "detail": "", "sides": [
            {"names": ["Carlos Alcaraz"], "score": None, "winner": state == "post",
             "sets": [{"games": 6, "won": True, "tiebreak": None}, {"games": 3, "won": False, "tiebreak": None}]},
            {"names": ["Matteo Berrettini"], "score": None, "winner": False,
             "sets": [{"games": 4, "won": False, "tiebreak": None}, {"games": 2, "won": False, "tiebreak": None}]}]}

    def test_sets_and_order_flip(self):
        p = play(self.G, self.G, "Carlos Alcaraz", "ATP")
        hit = L.match(p, [self.contest()])
        self.assertIsNotNone(hit)
        self.assertEqual(L.status(p, *hit), "Leads 1-0 in sets")
        self.assertEqual(L.score_text(p, *hit), "4-6 2-3")

    def test_final(self):
        p = play(self.G, self.G, "Matteo Berrettini", "ATP")
        hit = L.match(p, [self.contest("post")])
        self.assertEqual(L.status(p, *hit), "Lost")


class Matching(unittest.TestCase):
    def test_wrong_day_game_is_rejected(self):
        p = play("Jazz vs. Pelicans", "Jazz vs. Pelicans", "Jazz")
        old = contest("Utah Jazz", 1, "New Orleans Pelicans", 2, start=NOW - timedelta(days=2))
        self.assertIsNone(L.match(p, [old]))

    def test_different_teams_are_rejected(self):
        p = play("Jazz vs. Pelicans", "Jazz vs. Pelicans", "Jazz")
        self.assertIsNone(L.match(p, [contest("Boston Celtics", 1, "New York Knicks", 2)]))

    def test_two_equal_games_are_not_guessed(self):
        p = play("Miami vs. Duke", "Miami vs. Duke", "Duke", "NCAAB")
        a, b = contest("Miami", 1, "Duke", 2), contest("Miami", 3, "Duke", 4)
        self.assertIsNone(L.match(p, [a, b]))

    def test_parse_scoreboard_keeps_only_singles(self):
        comp = {"competitors": [{"athlete": {"displayName": "A B"}}, {"athlete": {"displayName": "C D"}}],
                "status": {"type": {"state": "in", "shortDetail": "Set 2"}}, "date": "2026-11-05T01:00Z"}
        js = {"events": [{"groupings": [
            {"grouping": {"displayName": "Men's Singles"}, "competitions": [comp]},
            {"grouping": {"displayName": "Men's Doubles"}, "competitions": [comp]}]}]}
        self.assertEqual(len(L.parse_scoreboard(js, tennis=True)), 1)


if __name__ == "__main__":
    unittest.main()
