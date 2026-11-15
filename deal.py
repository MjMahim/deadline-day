"""What a transfer deal consists of, and what a given deal is worth to each party.

A real transfer is a package, not a single number.

Between the clubs:           fee      guaranteed fee (M)
                             add_ons  extra payments that depend on performance (M, at most half the fee)
                             years    the fee is paid in instalments over this many years (1 = all up front)
                             sell_on  the selling club's share of the profit on a future sale (%)
Between club and player:     salary   yearly salary (M)
                             years    length of the contract

Each party values the same package differently: a club in debt wants cash now, a rich club does not mind paying
early, a young player's sell-on clause is worth more than an old player's. `worth` turns a package into one
number for one party, so packages can be compared with that party's private limit.
"""

ADD_ON_SHARES = (0, 0.1, 0.2, 0.3)      # add-ons as a share of the guaranteed fee, as tried by the planner
INSTALMENT_YEARS = (1, 2, 3, 4)
SELL_ON = (0, 10, 20)
CONTRACT_YEARS = (1, 2, 3, 4, 5)
YEAR_PENALTY = 0.05                     # each contract year away from a party's preference costs 5% of the salary


def money(value):
    """Round the way people in football talk about money."""
    if value >= 10:
        return round(value * 2) / 2
    if value >= 2:
        return round(value, 1)
    return round(value * 20) / 20


def _number(value, default):
    try:
        return default if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return default


def normalise(kind, amount, given=None):
    """Build a complete package from a headline amount and optional extra terms. Raises ValueError if invalid."""
    given = given or {}
    if amount is None or amount <= 0:
        raise ValueError("an offer needs a positive amount")
    if kind == "fee":
        terms = {"fee": amount, "add_ons": round(_number(given.get("add_ons"), 0), 2),
                 "years": int(_number(given.get("years"), 1)), "sell_on": int(_number(given.get("sell_on"), 0))}
        if not 0 <= terms["add_ons"] <= 0.5 * amount:
            raise ValueError("add-ons must be between 0 and half the guaranteed fee")
        if not 1 <= terms["years"] <= 4:
            raise ValueError("instalments must be spread over 1 to 4 years")
        if not 0 <= terms["sell_on"] <= 25:
            raise ValueError("a sell-on clause must be between 0 and 25 percent")
        return terms
    terms = {"salary": amount, "years": int(_number(given.get("years"), 4))}
    if not 1 <= terms["years"] <= 5:
        raise ValueError("a contract must be 1 to 5 years long")
    return terms


def describe(kind, terms):
    """A package in words, e.g. "40M plus up to 6M in add-ons, paid over 3 years, 10% sell-on"."""
    if kind == "salary":
        return f"{terms['salary']:g}M a year on a {terms['years']}-year contract"
    text = f"{terms['fee']:g}M"
    if terms["add_ons"]:
        text += f" plus up to {terms['add_ons']:g}M in add-ons"
    if terms["years"] > 1:
        text += f", paid over {terms['years']} years"
    if terms["sell_on"]:
        text += f", {terms['sell_on']}% sell-on"
    return text


def headline(kind, terms):
    return terms["fee"] if kind == "fee" else terms["salary"]


def worth(profile, kind, terms):
    """What a package is worth to the party with this profile, as one number in millions.

    For a club paying, this is what the package costs it; for a party receiving, what it brings in.
    """
    prefs = profile["prefs"]
    if kind == "fee":
        return (terms["fee"] * (1 - prefs["delay"] * (terms["years"] - 1))
                + prefs["add_on"] * terms["add_ons"] + terms["sell_on"] / 100 * prefs["resale"])
    away = abs(terms["years"] - prefs["contract_years"])
    sign = 1 if profile["role"] == "buyer" else -1      # a contract of the wrong length costs the club, hurts the player
    return terms["salary"] * (1 + sign * YEAR_PENALTY * away)
