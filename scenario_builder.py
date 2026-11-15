"""Turn a description of a real-life transfer situation into a negotiation scenario.

The same things that drive a real transfer drive the numbers here: the player's position, age, form, fitness
and contract length set his market value; each club's situation sets how far it will go and how it values
instalments, add-ons and a sell-on clause; the player's attitude and age set his wage demands and the contract
length he wants. The factors are rough rules of thumb, not fitted to real transfer data.

All money is in millions of euros.
"""
from deal import money

# Each choice: label shown in the menu -> factor(s) used below.
OPTIONS = {
    # Age is chosen as a band, because that is how it affects value: a 22-year-old and a 25-year-old are priced
    # alike. Each band stands for one typical age, used for the wording and the player card.
    "age": {"17-21 (young talent)": 19, "22-25 (rising)": 24, "26-28 (peak years)": 27, "29-30": 30,
            "31-32": 31, "33 and over (veteran)": 34},
    "position": {"Goalkeeper": 0.6, "Defender": 0.8, "Midfielder": 1.0, "Winger": 1.1, "Striker": 1.2},
    "performance": {            # base market value of a 26-28 year old midfielder with 3 years of contract
        "Squad player": 8, "Solid starter": 20, "Key player": 40, "Star of the league": 75, "World class": 120},
    "contract": {               # less time left on the contract means less power for the selling club
        "6 months left": 0.45, "1 year left": 0.7, "2 years left": 0.9, "3 years left": 1.0,
        "4 or more years left": 1.1},
    "injuries": {               # (effect on value, chance the medical passes, chance it raises a concern)
        "No injury problems": (1.0, 0.97, 0.03), "A few injuries": (0.92, 0.80, 0.17),
        "Serious injury last season": (0.75, 0.55, 0.33)},
    "seller_stance": {          # (lowest fee, asking fee) as multiples of market value, (cost of waiting for
        #                          instalments per year, how much of an add-on's face value it counts)
        "Must sell (needs the money)": (0.8, 1.3, 0.06, 0.35), "Open to offers": (0.95, 1.4, 0.03, 0.55),
        "Does not want to sell": (1.25, 1.8, 0.02, 0.55)},
    "buyer_need": {             # how much the player is worth to the buyer, as a multiple of market value
        "Nice to have": 0.95, "A main target": 1.1, "Desperate (injury crisis)": 1.3},
    "buyer_finances": {         # (transfer budget, highest salary) as multiples of market value / market wage,
        #                          (saving per year of instalments, share of resale profit it cares about)
        "Tight budget": (1.0, 1.1, 0.06, 0.9), "Comfortable": (1.3, 1.5, 0.03, 0.9),
        "Very rich": (2.0, 2.2, 0.01, 0.5)},
    "player_attitude": {        # (lowest salary he accepts as a multiple of his market wage, effect on the
        #                          fee his club can hold out for, how much he values staying at his club)
        "Pushing for the move": (0.8, 0.88, 0.75), "Open to it": (1.0, 1.0, 0.95),
        "Happy where he is": (1.3, 1.12, 2.0)},      # a happy player stays if his club wants to keep him
    # The release clause is a fixed amount written into his contract when he signed it. It has nothing to do
    # with what he is worth today: it can be far above his value, or below it if he has improved since.
    "release_clause": {f"{amount}M": amount for amount in (5, 10, 20, 30, 40, 50, 60, 70, 80, 100, 120, 150, 200)},
    "rivals": {                 # how many other clubs are in the race: 0 none, 1 one club, 2 a bidding war
        "No other club": 0, "One other club is interested": 1, "Several clubs are bidding": 2},
}

PRESETS = {
    "Leo Silva (striker, 24, key player)": {
        "player_name": "Leo Silva", "position": "Striker", "age": "22-25 (rising)", "performance": "Key player",
        "contract": "2 years left", "injuries": "No injury problems", "release_clause": "80M"},
    "Kofi Mensah (winger, 19, rising star)": {
        "player_name": "Kofi Mensah", "position": "Winger", "age": "17-21 (young talent)", "performance": "Solid starter",
        "contract": "4 or more years left", "injuries": "No injury problems", "release_clause": "60M"},
    "Daniel Brandt (midfielder, 27, star of the league)": {
        "player_name": "Daniel Brandt", "position": "Midfielder", "age": "26-28 (peak years)", "performance": "Star of the league",
        "contract": "1 year left", "injuries": "A few injuries", "release_clause": "70M"},
    "Tomas Varga (goalkeeper, 31, solid starter)": {
        "player_name": "Tomas Varga", "position": "Goalkeeper", "age": "31-32", "performance": "Solid starter",
        "contract": "6 months left", "injuries": "No injury problems", "release_clause": "5M"},
    "Rafael Costa (defender, 27, key player, injured last season)": {
        "player_name": "Rafael Costa", "position": "Defender", "age": "26-28 (peak years)", "performance": "Key player",
        "contract": "3 years left", "injuries": "Serious injury last season", "release_clause": "40M"},
}

DEFAULTS = {
    "player_name": "Leo Silva", "position": "Striker", "age": "22-25 (rising)", "performance": "Key player",
    "contract": "2 years left", "injuries": "No injury problems",
    "seller_stance": "Must sell (needs the money)", "buyer_need": "A main target",
    "buyer_finances": "Comfortable", "player_attitude": "Open to it", "rivals": "No other club",
    "release_clause": "80M",
    "buyer_name": "Northbridge FC", "seller_name": "Riverside FC",
}

# The other clubs that may join the race. One is used for "one other club", all three for a bidding war.
RIVAL_CLUBS = [{"name": "Eastgate United", "short": "Eastgate", "letter": "E", "color": "#9b4dca"},
               {"name": "Westhaven City", "short": "Westhaven", "letter": "W", "color": "#0e9aa7"},
               {"name": "Southport Athletic", "short": "Southport", "letter": "S", "color": "#c2255c"}]


# How much of the youth premium a player earns: only a young player who is already performing is worth more
# for being young. A young squad player has not shown that his future is worth paying for.
YOUTH_CREDIT = {"Squad player": 0.25, "Solid starter": 0.5, "Key player": 1.0, "Star of the league": 1.0,
                "World class": 1.0}


def age_factor(age, performance):
    """How age changes a player's value. Level (performance) sets the value; age only adjusts it.

    Up to 28 a buyer pays a premium for the seasons still ahead and the chance to sell him on, largest for
    22-25 year olds. After 28 the value falls quickly, whatever the level."""
    for limit, premium in ((21, 0.10), (25, 0.20), (28, 0.10)):
        if age <= limit:
            return 1 + premium * YOUTH_CREDIT[performance]
    for limit, factor in ((30, 0.75), (32, 0.5)):
        if age <= limit:
            return factor
    return 0.25


def by_age(age, table):
    """Pick the value for the first age bracket the player fits in."""
    return next(value for limit, value in table if age <= limit)


def build_scenario(choices=None):
    """Return (scenario, facts). `facts` are the public figures shown to the user."""
    c = {**DEFAULTS, **(choices or {})}
    age = OPTIONS["age"][c["age"]] if c["age"] in OPTIONS["age"] else int(c["age"])   # a band, or an exact age
    injury_value, medical_pass, medical_concern = OPTIONS["injuries"][c["injuries"]]
    value = (OPTIONS["performance"][c["performance"]] * OPTIONS["position"][c["position"]]
             * age_factor(age, c["performance"]) * OPTIONS["contract"][c["contract"]] * injury_value)
    wage = max(value * 0.1, 0.3)              # a player's yearly salary is roughly a tenth of his value
    resale = value * by_age(age, ((23, 0.6), (27, 0.3), (30, 0.1), (99, 0.02)))   # expected profit on a later sale

    # A player who is pushing to leave weakens his club's hand; one who is happy to stay strengthens it.
    salary_factor_player, pull, loyalty = OPTIONS["player_attitude"][c["player_attitude"]]
    clause = OPTIONS["release_clause"][c["release_clause"]]
    low, ask, seller_delay, seller_add_on = OPTIONS["seller_stance"][c["seller_stance"]]
    min_fee = money(value * low * pull)
    # Nobody pays more than the release clause, so asking for more is pointless - unless the club would not
    # sell even at that price, in which case only the clause itself can take the player away.
    asking_fee = max(min(money(value * ask * pull), clause), min_fee)
    # How much each party wants this settled quickly (0 = no hurry, 1 = wants it done now). A deal between
    # parties in a hurry closes early in the day; one between patient parties goes down to the deadline.
    hurry = {"buyer": {"Nice to have": 0.0, "A main target": 0.4, "Desperate (injury crisis)": 0.9}[c["buyer_need"]],
             "seller": {"Must sell (needs the money)": 0.7, "Open to offers": 0.3,
                        "Does not want to sell": 0.0}[c["seller_stance"]],
             "player_agent": {"Pushing for the move": 0.8, "Open to it": 0.4,
                              "Happy where he is": 0.0}[c["player_attitude"]]}
    # What the club would pay him to stay if the release clause is triggered (0: it will not try).
    keep = {"Must sell (needs the money)": 0, "Open to offers": 1.15, "Does not want to sell": 1.4}[c["seller_stance"]]

    budget_factor, salary_factor, buyer_delay, resale_share = OPTIONS["buyer_finances"][c["buyer_finances"]]
    budget = money(value * budget_factor)
    valuation = min(money(value * OPTIONS["buyer_need"][c["buyer_need"]]), budget)
    max_salary = money(wage * salary_factor)

    min_salary = money(wage * salary_factor_player)
    target_salary = money(min_salary * 1.5)

    name, buyer, seller = c["player_name"].strip() or "The player", c["buyer_name"].strip(), c["seller_name"].strip()
    surname = name.split()[-1]
    agent = f"{surname}'s agent"
    fitness = {"No injury problems": "", "A few injuries": " He has had a few injuries.",
               "Serious injury last season": " He had a serious injury last season."}[c["injuries"]]
    public = (f"{name} is a {age}-year-old {c['position'].lower()}, rated as: {c['performance'].lower()}. "
              f"He has {c['contract']} on his contract at {seller}, with a release clause of {clause:g}M.{fitness}")

    stance_text = {"Must sell (needs the money)": "The club needs money now to cover debts, so selling matters "
                                                  "and cash up front matters.",
                   "Open to offers": "The club does not need to sell but will listen to a good offer.",
                   "Does not want to sell": "The club wants to keep him and will only sell for a very high fee."
                   }[c["seller_stance"]]
    need_text = {"Nice to have": "He would improve the squad, but you can live without him.",
                 "A main target": "He is one of your main targets in this window.",
                 "Desperate (injury crisis)": "Your first-choice player in his position is injured, so signing "
                                              "him today is your top priority."}[c["buyer_need"]]
    attitude_text = {"Pushing for the move": f"{surname} badly wants this move.",
                     "Open to it": f"{surname} is open to the move if the terms are right.",
                     "Happy where he is": f"{surname} is happy at {seller} and will only leave for a big raise."
                     }[c["player_attitude"]]

    scenario = {
        "rounds": 10,              # one turn per hour of deadline day, 14:00 to 23:00
        "start_hour": 14,
        "market": {"value": money(value), "wage": money(wage)},
        "medical": {"pass": medical_pass, "concern": medical_concern},
        "rivals": {"level": OPTIONS["rivals"][c["rivals"]],
                   "clubs": RIVAL_CLUBS[:{0: 0, 1: 1, 2: 3}[OPTIONS["rivals"][c["rivals"]]]]},
        "player": {"name": name, "position": c["position"].lower(), "age": age, "club": seller,
                   "min_salary": min_salary, "release_clause": clause,
                   "loyalty": loyalty},      # how a salary at his current club compares with one elsewhere
        "agents": {
            buyer: {"role": "buyer", "short": buyer.split()[0], "hurry": hurry["buyer"], "transfer_budget": budget,
                    "valuation": valuation, "max_salary": max_salary, "brief": f"{need_text} {public}",
                    "prefs": {"delay": buyer_delay, "add_on": 0.4, "resale": round(resale * resale_share, 2),
                              "contract_years": by_age(age, ((25, 5), (29, 4), (31, 2), (99, 1)))}},
            seller: {"role": "seller", "short": seller.split()[0], "hurry": hurry["seller"],
                     "min_fee": min_fee, "asking_fee": asking_fee,
                     "keep_salary": money(wage * keep) if keep else 0,
                     "brief": f"{stance_text} {public}",
                     "prefs": {"delay": seller_delay, "add_on": seller_add_on, "resale": round(resale, 2),
                               "contract_years": by_age(age, ((25, 5), (29, 4), (31, 2), (99, 1)))}},
            agent: {"role": "player_agent", "short": "Agent", "hurry": hurry["player_agent"],
                    "target_salary": target_salary,
                    "brief": f"You earn a 10% commission on his salary, so you want it as high as possible. "
                             f"{attitude_text} {public}",
                    "prefs": {"contract_years": by_age(age, ((23, 3), (28, 4), (99, 3)))}},
        },
        # A fictional transfer journalist. Agents can leak tips to him; everyone reads his posts.
        "reporter": {"name": "Marco Fontana", "short": "Fontana"},
    }
    facts = {"summary": public, "market_value": money(value), "market_wage": money(wage),
             "release_clause": clause,
             "deal_possible": (min_fee <= valuation or clause <= valuation) and min_salary <= max_salary}
    return scenario, facts


if __name__ == "__main__":
    import json
    scenario, facts = build_scenario()
    print(json.dumps(facts, indent=2))
    print(json.dumps(scenario["agents"], indent=2))
