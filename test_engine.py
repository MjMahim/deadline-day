"""Checks for the negotiation engine and the planner agent. No language model is needed.

    python test_engine.py
"""
from agents import FactReporter
from deal import describe, normalise, worth
from market import Market
from scenario_builder import PRESETS, build_scenario

ALL_PLANNERS = ["buyer", "seller", "player_agent"]


def market(choices=None, seed=1, planner=ALL_PLANNERS):
    scenario, facts = build_scenario(choices)
    m = Market(scenario, seed=seed, verbose=False, planner_roles=planner)
    m.journalist = FactReporter()
    for agent in m.agents.values():
        if hasattr(agent, "use_llm"):
            agent.use_llm = False
    return m, facts


def test_packages():
    terms = normalise("fee", 40, {"add_ons": 10, "years": 3, "sell_on": 10})
    assert describe("fee", terms) == "40M plus up to 10M in add-ons, paid over 3 years, 10% sell-on"
    assert describe("salary", normalise("salary", 6, {})) == "6M a year on a 4-year contract"
    for bad in ({"add_ons": 30}, {"years": 7}, {"sell_on": 60}):
        try:
            normalise("fee", 40, bad)
            raise AssertionError(f"{bad} should be refused")
        except ValueError:
            pass
    seller = {"role": "seller", "prefs": {"delay": 0.06, "add_on": 0.35, "resale": 10}}
    assert round(worth(seller, "fee", terms), 2) == round(40 * (1 - 0.06 * 2) + 0.35 * 10 + 0.1 * 10, 2)
    club = {"role": "buyer", "prefs": {"contract_years": 5}}
    player = {"role": "player_agent", "prefs": {"contract_years": 3}}
    assert worth(club, "salary", {"salary": 10, "years": 3}) > 10       # the wrong length costs the club
    assert worth(player, "salary", {"salary": 10, "years": 5}) < 10     # and is worth less to the player


def test_rules():
    m, _ = market()
    buyer, seller, agent = m.buyer, m.seller, m.agent
    budget = m.profiles[buyer]["transfer_budget"]

    def do(name, to, kind, amount=None, **terms):
        m.apply(name, {"to": to, "type": kind, "amount": amount, "terms": terms, "message": "x"}, "14:00")
        return m.transcript[-1]

    assert not do(buyer, seller, "offer", budget + 1)["valid"], "an offer above the budget must be refused"
    assert not do(buyer, seller, "offer", budget - 1, add_ons=5)["valid"], "add-ons count towards the budget"
    assert not do(seller, agent, "message")["valid"], "the seller cannot contact the player's agent"
    assert not do(buyer, seller, "accept")["valid"], "there is nothing to accept yet"
    assert do(seller, buyer, "offer", 60)["valid"]
    assert do(buyer, seller, "offer", 40)["valid"]
    assert do(seller, buyer, "offer", 55)["valid"]
    assert not do(buyer, seller, "accept", 60)["valid"], "only the latest offer can be accepted"
    assert do(seller, buyer, "accept")["valid"] and m.fee.agreed["fee"] == 40, "the buyer's offer stayed open"
    assert not do(buyer, seller, "offer", 41)["valid"], "an agreed part cannot be reopened"
    assert do(agent, buyer, "offer", 6, years=4)["valid"]
    entry = do(buyer, agent, "offer", 6, years=4)
    assert entry["type"] == "accept" and m.salary.agreed, "offering exactly what was asked closes the deal"
    assert do(buyer, m.reporter, "message")["type"] == "leak"


def test_planner_negotiations():
    outcomes = set()
    for name, preset in PRESETS.items():
        for extra in ({}, {"rivals": "Several clubs are bidding"}, {"seller_stance": "Does not want to sell"}):
            for seed in (1, 2, 3):
                m, facts = market({**preset, **extra}, seed)
                o = m.run()
                where = f"{name}, {extra}, seed {seed}"
                outcomes.add(o["result"])
                assert o["invalid_actions"] == 0, f"invalid action in {where}"
                assert m.feed and m.feed[-1]["final"], f"no closing post in {where}"
                if o["result"] == "completed":
                    assert not o["overpaid"] and not o["seller_sold_below_minimum"], f"bad deal in {where}"
                    assert facts["deal_possible"], f"an impossible deal was agreed in {where}"
                else:
                    assert o["reason"], f"no reason given in {where}"
                # A club never takes back what it has already put on the table.
                bids = [x["terms"] for x in m.fee.offers if x["by"] == m.buyer]
                assert all(a["fee"] <= b["fee"] for a, b in zip(bids, bids[1:])), f"bid went down in {where}"
                asks = [x["terms"]["fee"] for x in m.fee.offers if x["by"] == m.seller]
                assert all(a >= b for a, b in zip(asks, asks[1:])), f"asking price went up in {where}"
                assert len(bids) <= 6 and len(asks) <= 6, f"too many offers for a realistic day in {where}"
    assert "completed" in outcomes and len(outcomes) > 1, f"expected deals and failures, got {outcomes}"


def average(values):
    return sum(values) / len(values)


def test_willingness_changes_the_fee():
    """A player pushing to leave weakens his club's hand; one happy to stay strengthens it."""
    cost = {}
    for attitude in ("Pushing for the move", "Happy where he is"):
        costs = []
        for seed in range(1, 9):
            m, _ = market({"player_attitude": attitude}, seed)
            o = m.run()
            if o["result"] == "completed":
                costs.append(o["buyer_cost"])
        cost[attitude] = average(costs)
    assert cost["Pushing for the move"] < cost["Happy where he is"], cost


def test_release_clause():
    # A club that will not sell, a buyer that needs the player badly, and a clause the buyer can afford.
    base = {"release_clause": "60M", "buyer_need": "Desperate (injury crisis)",
            "seller_stance": "Does not want to sell"}
    for seed in (1, 2, 3):
        # A rich buyer and a player who is open to the move: the clause is paid and the seller cannot stop it.
        m, facts = market({**base, "buyer_finances": "Very rich"}, seed)
        o = m.run()
        assert o["result"] == "completed" and o["clause_paid"], o
        assert o["fee"] == facts["release_clause"] and o["fee_terms"]["add_ons"] == 0
        assert m.profiles[m.seller]["min_fee"] > o["fee"], "the seller would have refused this fee in talks"
        assert o["invalid_actions"] == 0

        # A player who is happy where he is, and a buyer that cannot outbid his club's new contract: he stays.
        m, _ = market({**base, "buyer_finances": "Comfortable", "player_attitude": "Happy where he is"}, seed)
        o = m.run()
        assert o["clause_paid"] and o["result"] == "stayed (new contract)", o
        assert m.stay.offers and all(x["by"] == m.seller for x in m.stay.offers)
        assert not m.stay.offers or m.clause_hour <= m.stay.offers[0]["hour"], "stay talks open only after the clause"

    # Before the clause is paid the selling club cannot approach the player's agent, and only the buyer can pay it.
    m, _ = market(base)
    m.apply(m.seller, {"to": m.agent, "type": "offer", "amount": 9, "message": "stay"}, "14:00")
    assert not m.transcript[-1]["valid"]
    m.apply(m.seller, {"to": m.buyer, "type": "release", "message": "x"}, "14:00")
    assert not m.transcript[-1]["valid"]
    # In the hour the clause is paid, the buyer cannot yet close personal terms.
    m.apply(m.agent, {"to": m.buyer, "type": "offer", "amount": 6, "message": "x"}, "14:00")
    m.apply(m.buyer, {"to": m.seller, "type": "release", "message": "x"}, "14:00")
    assert m.clause_paid and m.fee.agreed["fee"] == m.scenario["player"]["release_clause"]
    m.apply(m.buyer, {"to": m.agent, "type": "accept", "message": "x"}, "14:00")
    assert not m.transcript[-1]["valid"] and m.salary.agreed is None
    m.apply(m.buyer, {"to": m.agent, "type": "accept", "message": "x"}, "15:00")
    assert m.transcript[-1]["valid"] and m.salary.agreed is not None


def test_rival_clubs():
    """Other interested clubs are named clubs with their own public bids, and one of them can win the player."""
    m, _ = market({"rivals": "One other club is interested"})
    assert len(m.rivals) == 1
    results = set()
    for seed in range(1, 9):
        m, _ = market({"rivals": "Several clubs are bidding"}, seed)
        assert len({rival.name for rival in m.rivals}) == 3
        o = m.run()
        results.add(o["result"])
        clause = m.scenario["player"]["release_clause"]
        assert all(rival.bid is None or rival.bid <= clause for rival in m.rivals)
        bids = [e["text"] for e in m.events if " bid " in e["text"] and any(r.name in e["text"] for r in m.rivals)]
        assert all("M" in text for text in bids), "rival bids are reported with their amount"
        if o["result"] == "joined rival":
            assert o["rival_club"] in {rival.name for rival in m.rivals}
            assert o["rival_fee"] >= m.profiles[m.seller]["min_fee"]
        if o["result"] == "completed":
            top = m.top_rival()     # the buying club only wins by beating every bid still on the table
            assert top is None or o["seller_value"] >= top.bid - 0.01
    assert "joined rival" in results, results
    m, _ = market({"rivals": "No other club"})
    assert not m.rivals and m.run()["result"] != "joined rival"


def test_deals_close_at_different_hours():
    """Parties in a hurry settle early in the day; patient ones go down to the deadline."""
    def deal_hours(choices):
        hours = []
        for seed in range(1, 9):
            m, _ = market(choices, seed)
            o = m.run()
            if o["result"] == "completed":
                hours.append(int(o["hour"].split(":")[0]))
        return hours
    hurried = deal_hours({"buyer_need": "Desperate (injury crisis)", "seller_stance": "Must sell (needs the money)",
                          "player_attitude": "Pushing for the move"})
    patient = deal_hours({"buyer_need": "Nice to have", "seller_stance": "Open to offers"})
    assert hurried and patient
    assert max(hurried) <= 20, hurried
    assert average(hurried) + 2 <= average(patient), (hurried, patient)


def test_happy_player_stays_when_his_club_wants_him():
    base = {"release_clause": "60M", "buyer_need": "Desperate (injury crisis)", "seller_stance": "Does not want to sell"}
    for finances in ("Comfortable", "Very rich"):
        for seed in (1, 2, 3):
            m, _ = market({**base, "buyer_finances": finances, "player_attitude": "Happy where he is"}, seed)
            assert m.run()["result"] == "stayed (new contract)", (finances, seed)
            m, _ = market({**base, "buyer_finances": finances, "player_attitude": "Open to it"}, seed)
            o = m.run()
            assert o["result"] == "completed" and o["clause_paid"], (finances, seed, o["result"])


def test_impossible_deal_fails():
    m, facts = market({"buyer_finances": "Tight budget", "player_attitude": "Happy where he is"})
    o = m.run()
    assert not facts["deal_possible"] and o["result"] != "completed"
    assert "personal terms" in o["reason"]


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok   {test.__name__}")
    print(f"\nAll {len(tests)} checks passed.")
