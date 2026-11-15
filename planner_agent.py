"""A negotiating agent inspired by CICERO (Meta FAIR, "Human-level play in the game of Diplomacy by combining
language models with strategic reasoning", Science, 2022).

CICERO's main idea is that the language model does not decide what to do. A separate strategic module models
the other players and chooses a plan (an "intent"); the language model then writes messages that fit the plan,
and filters throw away messages that contradict it. This agent applies the same split to a transfer:

    1. read    - the LLM reads the other side's new messages and labels their stance
    2. believe - a probability distribution over the other side's secret limit, updated from their offers
    3. plan    - ordinary code decides whether to wait, reject, bid, make a final offer or accept, and builds
                 the package (fee, add-ons, instalments, sell-on; salary, contract length) that costs us least
                 for what it gives the other side
                 It also decides whether to trigger the release clause (buyer) or to offer the player a new
                 contract to stay once the clause has been triggered (seller)
    4. write   - the LLM writes one sentence per action, conditioned on the plan
    5. filter  - messages that state an amount that is not part of the plan are rejected

Only steps 1 and 4 use the LLM. Every number comes from steps 2 and 3.

Like a real club, the agent does not move every hour. It makes a new offer only when its plan has moved by a
meaningful step, when something happens (a rival bid, the deadline), or to make a final offer; otherwise it
rejects what is on the table or simply waits.
"""
import math
import random
import re

from deal import ADD_ON_SHARES, CONTRACT_YEARS, INSTALMENT_YEARS, SELL_ON, YEAR_PENALTY, describe, money, worth
from llm import chat_json

GRID = 81            # number of candidate values for the other side's limit
PATIENCE = 0.95      # value of "keep negotiating" relative to a certain deal now
STANCE_ROOM = {"flexible": 1.5, "neutral": 1.0, "firm": 0.8}   # how a stance changes the room we expect


class Belief:
    """What we think the other side's secret limit is, as weights over a grid of candidate values.

    prior:      the limit lies beyond their best offer so far, and the expected room shrinks as the deadline
                approaches (early offers are far from the limit, late offers are close to it).
    likelihood: a side that concedes steadily is walking towards its limit, so the pace of its concessions
                shows where it is heading. Some offers are bluffs, so no single offer is trusted fully.
    """
    BLUFF = 0.3      # share of offers assumed to carry no information
    NOISE = 0.06     # tolerated deviation from a steady concession, as a share of the first offer

    def __init__(self, they_sell, rounds):
        self.they_sell = they_sell
        self.rounds = rounds
        self.values, self.weights = [], []

    def update(self, offers, stance="neutral"):
        """offers: what the other side's offers so far are worth to them, as (round, value), oldest first."""
        if not offers:
            return
        amounts = [amount for _, amount in offers]
        best = min(amounts) if self.they_sell else max(amounts)      # their offer that is best for us
        far = 0.5 * best if self.they_sell else 1.6 * best           # the most room we consider possible
        self.values = [best + (far - best) * i / (GRID - 1) for i in range(GRID)]

        time_left = 1 - offers[-1][0] / (self.rounds - 1)
        room = best * (0.05 + 0.3 * time_left) * STANCE_ROOM.get(stance, 1.0)
        first_round, first = offers[0]
        span = max(self.rounds - 1 - first_round, 1)
        noise = self.NOISE * first
        self.weights = []
        for value in self.values:
            weight = math.exp(-abs(value - best) / room)                         # prior
            for rnd, amount in offers[1:]:                                       # likelihood
                expected = first + (value - first) * (rnd - first_round) / span
                fit = math.exp(-0.5 * ((amount - expected) / noise) ** 2)
                weight *= (1 - self.BLUFF) * fit + self.BLUFF * 0.2
            self.weights.append(weight)
        total = sum(self.weights)
        self.weights = [w / total for w in self.weights]

    def known(self):
        return bool(self.values)

    def chance_they_take(self, amount):
        """Probability that a package worth `amount` to them is inside their limit."""
        ok = (lambda v: v <= amount) if self.they_sell else (lambda v: v >= amount)
        return sum(w for v, w in zip(self.values, self.weights) if ok(v))

    def mean(self):
        return sum(v * w for v, w in zip(self.values, self.weights))

    def quantile(self, share):
        """The value below which `share` of the belief lies (0.5 is the median)."""
        total = 0.0
        for value, weight in sorted(zip(self.values, self.weights)):
            total += weight
            if total >= share:
                return value
        return max(self.values)


class PlannerAgent:
    def __init__(self, name, scenario, seed=None, use_llm=True):
        self.name = name
        self.seed = seed
        self.use_llm = use_llm          # False: template messages only (for tests without the model)
        self.rounds = scenario["rounds"]
        self.start_hour = scenario["start_hour"]
        self.reporter = scenario["reporter"]["name"]
        self.profile = profile = scenario["agents"][name]
        self.role = profile["role"]
        self.pays = self.role == "buyer"
        player = scenario["player"]
        # Each negotiator has its own temperament, so two runs do not unfold the same way.
        rng = random.Random(f"{seed}-{name}")
        self.lateness = rng.uniform(1.6, 2.8)   # above 1: holds firm early and moves late, like real clubs
        # A party in a hurry concedes sooner and wants the deal closed hours before the deadline; a patient
        # one takes it to the wire. `done_by` is the hour (as a turn number) by which it wants to be finished.
        hurry = profile["hurry"]
        self.lateness = max(0.8, self.lateness * (1 - 0.6 * hurry))
        latest = self.rounds - 2 if self.role == "buyer" else self.rounds - 3     # leaves time for the medical
        self.done_by = round(latest - hurry * (latest - 2) * rng.uniform(0.6, 1.0))
        self.stay_seen = None                   # agent: when his club's offer to stay arrived
        self.clause_round = None                # buyer: when it triggered the release clause
        self.step = rng.uniform(0.07, 0.11)     # the smallest move worth making, as a share of the deal
        opening = rng.uniform(0.72, 0.82)       # a buyer's first bid, as a share of the player's market value
        # limit: the worst deal still worth taking. start: the opening offer.
        if self.role == "buyer":
            market = scenario["market"]
            self.limit = {"fee": profile["valuation"], "salary": profile["max_salary"]}
            self.start = {"fee": money(opening * min(profile["valuation"], market["value"])),
                          "salary": money(min((opening + 0.15) * market["wage"], profile["max_salary"]))}
            who = f"the sporting director of {name}, which wants to sign {player['name']}"
        elif self.role == "seller":
            self.limit, self.start = {"fee": profile["min_fee"]}, {"fee": profile["asking_fee"]}
            who = f"the sporting director of {name}, which is selling {player['name']}"
        else:
            self.limit, self.start = {"salary": player["min_salary"]}, {"salary": profile["target_salary"]}
            who = f"the football agent representing {player['name']}"
        self.clause = player["release_clause"]
        # Paying the clause only makes sense for a buyer that can afford it and values the player that much.
        self.clause_usable = (self.pays and self.clause <= profile["transfer_budget"]
                              and self.clause <= profile["valuation"])
        self.loyalty = player["loyalty"]             # what a salary at his current club is worth to the player
        self.keep = profile.get("keep_salary", 0)    # seller: the most it would pay him to stay
        self.stay_offers = 0
        self.outside = {}        # kind -> value of our alternative (a rival club's bid), if we have one
        self.assumed = {}        # counterpart -> our guess of how they value a package
        self.beliefs = {}        # counterpart -> Belief
        self.stance = {}         # counterpart -> "firm" | "neutral" | "flexible"
        self.state = {}          # counterpart -> progress of that negotiation
        self.sent = []           # our own earlier messages, so the wording does not repeat
        self.read_upto = 0       # how much of the inbox has been read
        self.rival_seen = None   # the rival bid we last reacted to
        self.to_beat = 0         # buyer: the best rival bid, which our own bid has to beat
        self.leaks = 0
        self.write_prompt = f"""You write the messages for {who}, on transfer deadline day.
The decisions are already made by the club's analysts and are final. Your only job is the wording.
{"You are the BUYING side: you want to pay less, and their prices are too high for you." if self.pays else
 "You are on the RECEIVING side: you want more money, and their offers are too low for you."}
Rules:
- Write ONE short sentence for each action, in the same order.
- Never repeat the other side's price or present it as your own position.
- Write the way a real negotiator texts the other side: natural and direct. Do not start with their name.
- Follow the stated purpose and tone of each action, but in your own words; do not copy the purpose text.
- Do not repeat the wording of your earlier messages.
- Mention no amount of money except the ones given for that action.
- Never reveal or hint at your own budget, valuation, minimum or maximum.

Reply ONLY with JSON in exactly this format:
{{"messages": ["<sentence for action 1>", "<sentence for action 2>"]}}"""
        self.read_prompt = f"""You are an analyst for {who}, on transfer deadline day.
You read what the other side wrote and judge how much room they still have to move.
- "firm": they sound like they will not move further
- "flexible": they sound eager, under pressure, or willing to move
- "neutral": cannot tell
Also note whether they say they need money quickly or up front.

Reply ONLY with JSON in exactly this format:
{{"stance": "firm|neutral|flexible", "wants_cash_now": true|false, "why": "<a few words>"}}"""

    # ---- what a package is worth ------------------------------------------------------------------------

    def guess(self, other, kind):
        """Our guess of the other side's profile. We do not know their preferences, so we assume typical ones."""
        if other not in self.assumed:
            mine = self.profile["prefs"]
            role = "buyer" if not self.pays else "seller" if kind == "fee" else "player_agent"
            self.assumed[other] = {"role": role, "prefs": {
                "delay": 0.03, "add_on": 0.5, "resale": mine.get("resale", 0), "contract_years": 4}}
        return self.assumed[other]

    def to_them(self, other, kind, terms):
        return worth(self.guess(other, kind), kind, terms)

    def gain(self, kind, terms):
        """How good a package is for us; 0 means no better than walking away (or taking the rival's bid)."""
        limit = self.limit[kind]
        if kind in self.outside:
            limit = max(limit, self.outside[kind])
        mine = worth(self.profile, kind, terms)
        return limit - mine if self.pays else mine - limit

    def forbidden(self, kind, terms):
        if not self.pays:
            return False
        if kind == "fee":
            return (terms["fee"] + terms["add_ons"] > self.profile["transfer_budget"]
                    or (self.clause_usable and worth(self.profile, kind, terms) > self.clause))   # the clause is cheaper
        return terms["salary"] > self.profile["max_salary"]

    def plain(self, kind, amount):
        """The simplest package: everything up front, no extras; our preferred contract length."""
        if kind == "fee":
            return {"fee": amount, "add_ons": 0.0, "years": 1, "sell_on": 0}
        return {"salary": amount, "years": self.profile["prefs"]["contract_years"]}

    def shapes(self, kind, bids_made):
        """The package structures we may use. First bids are simple; later bids get creative, as in real talks."""
        if kind == "salary":
            mine = self.profile["prefs"]["contract_years"]
            return [{"years": mine}] if bids_made == 0 else [{"years": y} for y in sorted(
                CONTRACT_YEARS, key=lambda y: abs(y - mine))]
        if bids_made == 0:
            return [{"share": 0, "years": 1, "sell_on": 0}]
        if bids_made == 1:
            return [{"share": share, "years": 1, "sell_on": 0} for share in ADD_ON_SHARES]
        return [{"share": share, "years": years, "sell_on": sell_on}
                for sell_on in SELL_ON for years in INSTALMENT_YEARS for share in ADD_ON_SHARES]

    def package(self, kind, other, value, bids_made=9, previous=None):
        """Among packages worth `value` to the other side, the one that is best for us.

        `previous` is our last offer. As in real talks, a new offer never takes back what was already on the
        table (a buyer does not lower its guaranteed fee), and the structure only changes when that clearly pays.
        """
        theirs = self.guess(other, kind)["prefs"]
        options = []
        for shape in self.shapes(kind, bids_made):
            if kind == "fee":
                factor = 1 - theirs["delay"] * (shape["years"] - 1) + theirs["add_on"] * shape["share"]
                fee = (value - shape["sell_on"] / 100 * theirs["resale"]) / factor
                if fee <= 0:
                    continue
                fee = money(fee)
                terms = {"fee": fee, "add_ons": money(shape["share"] * fee) if shape["share"] else 0.0,
                         "years": shape["years"], "sell_on": shape["sell_on"]}
            else:
                sign = 1 if self.guess(other, kind)["role"] == "buyer" else -1
                factor = 1 + sign * YEAR_PENALTY * abs(shape["years"] - theirs["contract_years"])
                terms = {"salary": money(value / factor), "years": shape["years"]}
            if not self.forbidden(kind, terms):
                options.append(terms)
        if previous:
            head = "fee" if kind == "fee" else "salary"
            total = lambda t: t[head] + t.get("add_ons", 0)
            if self.pays:
                kept = [t for t in options if t[head] >= previous[head] and total(t) >= total(previous)]
            else:
                kept = [t for t in options if t[head] <= previous[head] and total(t) <= total(previous)]
            if not kept and self.gain(kind, previous) >= 0:
                return previous                  # nothing better fits: stand by the last offer
            options = kept or options            # only a changed situation (a rival's bid) justifies going back
        if not options:
            return None
        best = max(options, key=lambda t: self.gain(kind, t))      # ties go to the simpler package, listed first
        if previous and kind == "fee":
            same = lambda t: (t["years"], t["sell_on"], bool(t["add_ons"])) == (
                previous["years"], previous["sell_on"], bool(previous["add_ons"]))
            familiar = [t for t in options if same(t)]
            if familiar:
                keep = max(familiar, key=lambda t: self.gain(kind, t))
                if self.gain(kind, best) - self.gain(kind, keep) < 0.01 * self.limit[kind]:
                    return keep
        return best

    def best_final_offer(self, kind, other, belief, start, edge):
        """The take-it-or-leave-it package with the highest expected gain: chance they take it x our gain.

        Returns (what it is worth to them, expected gain)."""
        end = edge * (1.15 if self.pays else 0.85)
        best, best_value = None, 0.0
        for i in range(61):
            value = start + (end - start) * i / 60
            if kind == "fee" and not self.pays and value > self.clause:
                continue                 # no buyer pays more than the release clause
            if kind == "fee" and self.pays and value <= self.to_beat:
                continue                 # the seller already has a better bid from another club
            terms = self.package(kind, other, value)
            if terms is None or self.gain(kind, terms) < 0:
                continue
            expected = belief.chance_they_take(value) * self.gain(kind, terms)
            if expected > best_value:
                best, best_value = value, expected
        return (best, best_value) if best is not None else (edge, 0.0)

    # ---- step 1: read ---------------------------------------------------------------------------------

    def read(self, snapshot):
        new = snapshot["inbox"][self.read_upto:]
        self.read_upto = len(snapshot["inbox"])
        raws = []
        for other, neg in snapshot["negotiations"].items():
            texts = [f'[{e["hour"]}] "{e["message"]}"' for e in new if e["from"] == other and e["message"]]
            if not texts or not self.use_llm:
                continue
            reply, raw = chat_json(self.read_prompt, f"Messages from {other}:\n" + "\n".join(texts), seed=self.seed)
            raws.append(raw)
            reply = reply if isinstance(reply, dict) else {}
            stance = str(reply.get("stance", "")).lower().strip()
            if stance in STANCE_ROOM:
                self.stance[other] = stance
            if reply.get("wants_cash_now") is True and neg["kind"] == "fee":
                self.guess(other, "fee")["prefs"].update(delay=0.06, add_on=0.35)
        return raws

    # ---- steps 2 and 3: believe and plan ----------------------------------------------------------------

    def react_to_rival(self, snapshot):
        """Other clubs change what we can get elsewhere (seller, agent) or how much time we have (buyer)."""
        rival = snapshot["rival"]
        news = (rival["name"], rival["bid"]) if rival["bid"] else None
        if self.role == "seller":
            self.outside = {"fee": rival["bid"]} if rival["bid"] else {}
        elif self.role == "player_agent":
            self.outside = {"salary": 0.9 * rival["salary"]} if rival["salary"] else {}
        else:
            self.to_beat = rival["bid"] or 0     # the seller will not take less than its best other bid
        changed = news is not None and news != self.rival_seen
        self.rival_seen = news
        if changed and self.pays:
            self.lateness = 1.0          # a rival is bidding: no more waiting for the last hour
        return changed

    def plan(self, snapshot):
        """Return the intents for this hour (what to do and why) and a note per negotiation."""
        rnd = snapshot["round"]
        self.believed = []       # this hour's beliefs, kept for the record
        rival_news = self.react_to_rival(snapshot)
        # The buyer moves first each hour, so the others can still answer its last offer. For the seller and
        # the player's agent, an offer made in the last hour comes too late to be accepted.
        # Everyone aims to be done earlier still: to leave time for the medical, and sooner if in a hurry.
        own_final = self.done_by
        latest = self.rounds - 2 if self.pays else self.rounds - 3
        if self.pays and snapshot["clause"]["paid"]:
            # The clause is triggered: the player is weighing our terms against his club's, so there is no
            # time to haggle. Put the best personal terms on the table now.
            if self.clause_round is None:
                self.clause_round = rnd
            own_final = min(own_final, self.clause_round)
        negotiations = snapshot["negotiations"]
        fee_talks = next((n for n in negotiations.values() if n["kind"] == "fee"), None)
        intents, notes = [], []

        stay = next((n for n in negotiations.values() if n["topic"] == "stay"), None)
        if self.role == "player_agent" and stay and stay["theirs"]:
            # His club's offer of a new contract is an alternative to moving.
            staying = self.loyalty * worth(self.profile, "salary", stay["theirs"]["terms"])
            self.outside["salary"] = max(self.outside.get("salary", 0), staying)
            talks = next((n for n in negotiations.values() if n["topic"] == "salary"), None)
            # If staying is worth far more to him than anything he even hoped to get from the move, there is
            # nothing to negotiate: he tells the buying club so and signs with his own club.
            if staying > 1.25 * self.start["salary"] and stay["agreed"] is None and talks and talks["agreed"] is None:
                buyer = next(o for o, n in negotiations.items() if n["topic"] == "salary")
                seller = next(o for o, n in negotiations.items() if n["topic"] == "stay")
                intents.append({"to": buyer, "kind": "salary", "type": "message", "terms": None, "text": "",
                                "tone": "polite and final",
                                "purpose": "thank them for their interest and tell them the player has decided "
                                           "to stay at his club; this is not about money"})
                intents.append({"to": seller, "kind": "stay", "type": "accept", "terms": stay["theirs"]["terms"],
                                "text": describe("salary", stay["theirs"]["terms"]), "tone": "warm",
                                "purpose": "accept the new contract and tell them he is staying"})
                notes.append("his club wants him and he is happy there: staying is worth far more to him than "
                             "this move could be, so he turns the buying club down and signs the new contract")
                return intents, notes

        for other, neg in negotiations.items():
            kind = neg["kind"]
            label = "fee" if kind == "fee" else "personal terms"
            final = own_final
            deadline_near = rnd >= latest
            if neg["topic"] == "stay":
                self.plan_stay(other, neg, rnd, intents, notes)
                continue
            if neg["agreed"] is not None:
                continue
            state = self.state.setdefault(other, {"opened": None, "seen": None, "final_sent": False, "pushed": set(),
                                                  "clock": None, "last_offer": None})
            theirs, mine = neg["theirs"], neg["mine"]
            seen = (theirs["hour"], theirs["amount"]) if theirs else None
            new_from_them, state["seen"] = seen != state["seen"], seen

            # When to start talking, as in real life.
            if state["opened"] is None:
                if self.role == "seller":
                    ready = theirs is not None                       # a seller waits to be approached
                elif self.role == "player_agent":
                    ready = theirs is not None or rnd >= 1           # the agent makes his demands known early
                elif kind == "fee":
                    ready = True
                else:                                                # personal terms follow the fee
                    close = (fee_talks and fee_talks["mine"] and fee_talks["theirs"] and
                             fee_talks["mine"]["amount"] >= 0.85 * fee_talks["theirs"]["amount"])
                    ready = (fee_talks is None or fee_talks["agreed"] is not None or close
                             or rnd >= max(final - 2, 1))
                if not ready:
                    notes.append(f"{label}: not started yet")
                    continue
                state["opened"] = rnd

            # Step 2: what do we believe about their limit?
            belief = self.beliefs.setdefault(other, Belief(they_sell=self.pays, rounds=self.rounds))
            their_offers = [(int(o["hour"].split(":")[0]) - self.start_hour, self.to_them(other, kind, o["terms"]))
                            for o in neg["offers"] if o["by"] == other]
            belief.update(their_offers, self.stance.get(other, "neutral"))
            bids_made = sum(o["by"] == self.name for o in neg["offers"])

            start = self.to_them(other, kind, self.plain(kind, self.start[kind]))
            last = self.to_them(other, kind, mine["terms"]) if mine else start
            limit = max(self.limit[kind], self.outside.get(kind, 0)) if not self.pays else self.limit[kind]
            edge = self.to_them(other, kind, self.plain(kind, limit))        # where our gain reaches zero
            # If something better has turned up elsewhere (a rival's bid, a new contract from his own club),
            # our earlier offer is now a bad deal for us and has to be withdrawn.
            stale = mine is not None and self.gain(kind, mine["terms"]) < 0
            if stale:
                last = edge
            if belief.known():
                self.believed.append({"about": other, "kind": kind, "mean": round(belief.mean(), 2),
                                      "low": round(belief.quantile(0.1), 2), "high": round(belief.quantile(0.9), 2)})
                goal, goal_value = self.best_final_offer(kind, other, belief, last, edge)
                note = (f"{label}: their limit is probably near {belief.mean():.1f} "
                        f"(stance: {self.stance.get(other, 'neutral')}), best final offer worth {goal:.1f} to them")
            else:
                goal, goal_value = (start + edge) / 2, None
                note = f"{label}: no offer from them yet"

            # Step 3: where should our offer be by now? Hold early, move late; never go back.
            # Our clock only starts once the other side is at the table (the buyer starts it by bidding).
            if state["clock"] is None and (self.pays or theirs is not None):
                state["clock"] = rnd
            clock = state["clock"] if state["clock"] is not None else rnd
            span = max(final - clock, 1)
            progress = min(max((rnd - clock) / span, 0), 1)
            target = start + (goal - start) * progress ** self.lateness
            target = max(target, last) if self.pays else min(target, last)
            last_call = rnd >= final
            if last_call:
                target = max(goal, last) if self.pays else min(goal, last)
            if self.pays and kind == "fee" and self.to_beat and mine is not None:
                target = max(target, min(self.to_beat * 1.02, goal))     # a bid below a rival's is pointless
            # With the clause triggered, the player is comparing us with his own club. If he has not said yes
            # an hour later, we go to the most we can pay rather than lose him over wages.
            all_in = (self.pays and kind == "salary" and self.clause_round is not None
                      and rnd > self.clause_round and not state.get("all_in"))
            if all_in:
                state["all_in"] = True
                target = max(edge, last)
            # Our own limit as a round figure that is still (just) worth taking.
            at_limit = self.plain(kind, math.floor(limit * 10) / 10 if self.pays else math.ceil(limit * 10) / 10)
            terms = (self.package(kind, other, target, bids_made, mine["terms"] if mine and not stale else None)
                     or at_limit)
            if self.gain(kind, terms) < 0:       # never put a losing deal on the table
                terms = mine["terms"] if mine and not stale else at_limit

            intent = {"to": other, "kind": kind}
            # In the hour we pay the clause, the player first listens to his own club: no deal with him yet.
            hold = self.pays and kind == "salary" and any(i["type"] == "release" for i in intents)
            if hold:
                note += "; the player will first hear from his club"
            if theirs and not hold and self.gain(kind, theirs["terms"]) >= 0                     and not self.forbidden(kind, theirs["terms"]):
                their_gain = self.gain(kind, theirs["terms"])
                keep_going = PATIENCE * goal_value if goal_value is not None else float("inf")
                no_better = self.gain(kind, terms) <= their_gain
                if rnd > final or no_better or their_gain >= keep_going:
                    reason = ("we want this done now, and a deal beats no deal" if rnd > final else
                              "it is at least as good as what we were about to ask" if no_better else
                              "it is worth more than the expected value of holding out")
                    intents.append({**intent, "type": "accept", "terms": theirs["terms"],
                                    "text": describe(kind, theirs["terms"]), "tone": "pleased",
                                    "purpose": "accept their offer and close this part of the deal"})
                    notes.append(f"{note}; accept their offer because {reason}")
                    continue

            # A new offer needs a meaningful move and some time since the last one, unless something forces it.
            rested = state["last_offer"] is None or rnd - state["last_offer"] >= 2
            # Paying the release clause ends the fee talks at once: the seller cannot refuse.
            if self.clause_usable and kind == "fee":
                clause_gain = self.limit["fee"] - self.clause
                why = ("another club is bidding, and the clause shuts them out" if rival_news else
                       "time is nearly up and the clause is the only sure way" if rnd == self.rounds - 1 else
                       "it is worth more than gambling on a final offer"
                       if last_call and (goal_value is None or clause_gain >= goal_value) else None)
                if why:
                    intents.append({**intent, "type": "release", "terms": self.plain("fee", self.clause),
                                    "text": f"release clause of {self.clause:g}M", "tone": "polite but final",
                                    "purpose": "tell them we are triggering the release clause and will pay it "
                                               "in full, so the fee is no longer up for discussion"})
                    notes.append(f"{note}; trigger the release clause of {self.clause:g}M because {why}")
                    self.clause_round = rnd
                    continue

            moved = abs(target - last) >= self.step * abs(edge) and rested
            urgent = rival_news and self.pays and kind == "fee" and mine is not None
            too_late = not self.pays and rnd == self.rounds - 1      # nobody is left to answer
            # Beyond ordinary moves there are only a few moments to push: when we wanted to be done, when the
            # medical is about to become impossible, and the very last hour in which they can still answer.
            moments = {final, latest, self.rounds - 1 if self.pays else self.rounds - 2}
            push = last_call and rnd in moments and rnd not in state["pushed"]
            if push:
                state["pushed"].add(rnd)
            if not too_late and (mine is None or moved or urgent or push or stale or all_in) \
                    and (mine is None or terms != mine["terms"])                     and not (hold and theirs and terms == theirs["terms"]):
                state["final_sent"] = state["final_sent"] or last_call
                state["last_offer"] = rnd
                verb = "bid" if self.pays else "demand" if kind == "salary" else "asking price"
                extras = [word for word, used in (("add-ons", kind == "fee" and terms["add_ons"]),
                                                  ("instalments", kind == "fee" and terms["years"] > 1),
                                                  ("a sell-on clause", kind == "fee" and terms["sell_on"])) if used]
                if all_in:
                    tone, purpose = "decisive", ("make our best and last personal terms: we have triggered his "
                                                 "release clause and want him to choose us over his club")
                elif push and deadline_near:
                    tone, purpose = "final offer", f"make clear this is our final {verb} before the window closes"
                elif push:
                    tone, purpose = "pressing", f"put our best {verb} on the table and push to get this done now"
                elif mine is None and theirs is not None and not self.pays:
                    tone, purpose = "unimpressed", f"turn down their offer as too low and state our {verb}"
                elif mine is None:
                    tone, purpose = "confident", f"make our opening {verb}"
                elif stale:
                    tone, purpose = "firm", (f"withdraw our earlier {verb} and replace it, because we now have a "
                                             f"better alternative elsewhere")
                elif urgent:
                    tone, purpose = "decisive", f"improve our {verb} at once because another club is bidding"
                else:
                    tone, purpose = "constructive", (f"{'raise' if self.pays else 'lower'} our {verb} in a "
                                                     f"meaningful step and ask them to respond")
                if extras and mine is not None:
                    purpose += f"; the package now includes {' and '.join(extras)}"
                intents.append({**intent, "type": "offer", "terms": terms, "text": describe(kind, terms),
                                "tone": tone, "purpose": purpose})
                notes.append(f"{note}; offer {describe(kind, terms)}")
            elif new_from_them and theirs is not None:
                tone = "firm" if not too_late else "regretful"
                purpose = ("turn down their latest offer without making a new one; it is "
                           f"{'still too much' if self.pays else 'not enough'}")
                intents.append({**intent, "type": "message", "terms": None, "text": "", "tone": tone,
                                "purpose": purpose})
                notes.append(f"{note}; reject their offer and hold our position")
            else:
                notes.append(f"{note}; wait, the next move is theirs")

        # The player's decision. Once the buying club has had its chance to answer his club's offer (or the
        # day is ending), he stops holding out and takes the better of the two: the move, if the terms on the
        # table beat staying, and otherwise the new contract at his own club.
        if stay and stay["theirs"] and self.stay_seen is None:
            self.stay_seen = rnd
        if (self.role == "player_agent" and stay and stay["theirs"] and stay["agreed"] is None
                and (rnd == self.rounds - 1 or rnd >= self.stay_seen + 2)
                and not any(i["type"] == "accept" for i in intents)):
            buyer, talks = next((o, n) for o, n in negotiations.items() if n["topic"] == "salary")
            seller = next(o for o, n in negotiations.items() if n["topic"] == "stay")
            offer = talks["theirs"]
            if talks["agreed"] is None and offer and self.gain("salary", offer["terms"]) >= 0:
                intents[:] = [i for i in intents if i["to"] != buyer]
                intents.append({"to": buyer, "kind": "salary", "type": "accept", "terms": offer["terms"],
                                "text": describe("salary", offer["terms"]), "tone": "pleased",
                                "purpose": "accept their terms: the player has chosen the move"})
                notes.append("decision time: the move beats his club's new contract, so he accepts it")
            elif talks["agreed"] is None:
                intents.append({"to": seller, "kind": "stay", "type": "accept", "terms": stay["theirs"]["terms"],
                                "text": describe("salary", stay["theirs"]["terms"]), "tone": "warm",
                                "purpose": "accept the new contract and tell them he is staying"})
                notes.append("decision time: nothing on the table beats his club's new contract, so he stays")

        self.use_rival(snapshot, intents, rival_news, notes)
        return intents, notes

    def plan_stay(self, other, neg, rnd, intents, notes):
        """After the release clause is triggered, the selling club can only try to talk the player into staying."""
        if self.role != "seller" or neg["agreed"] is not None:
            return
        if not self.keep:
            notes.append("release clause triggered: we need the money, so we let him go")
            return
        last_chance = rnd >= self.rounds - 2
        if self.stay_offers <= 1:           # a first offer, then one improved offer the hour after
            salary = money(self.keep if self.stay_offers or last_chance else 0.9 * self.keep)
            terms = {"salary": salary, "years": self.profile["prefs"]["contract_years"]}
            if neg["mine"] and neg["mine"]["terms"] == terms:
                return
            self.stay_offers += 1
            intents.append({"to": other, "kind": "stay", "type": "offer", "terms": terms,
                            "text": describe("salary", terms), "tone": "warm and persuasive",
                            "purpose": "persuade the player to stay: tell him he is central to the club's plans "
                                       "and offer him a new contract"})
            notes.append(f"release clause triggered: our only move is to keep the player; "
                         f"offer {describe('salary', terms)}")
        else:
            notes.append("release clause triggered: our offer to the player stands; wait for his decision")

    def use_rival(self, snapshot, intents, rival_news, notes):
        """A seller or agent with another club's bid in hand tells the buyer, and may tip off the press."""
        if not rival_news or self.pays or not self.outside:
            return
        buyer, neg = next(iter(snapshot["negotiations"].items()))
        if neg["agreed"] is not None:
            return
        hint = f"; point out that {snapshot['rival']['name']} are bidding too, so they must do better"
        for intent in intents:
            if intent["to"] == buyer and intent["type"] != "accept":
                intent["purpose"] += hint
                break
        else:
            intents.append({"to": buyer, "kind": neg["kind"], "type": "message", "terms": None, "text": "",
                            "tone": "matter-of-fact", "purpose": "tell them the situation has changed" + hint})
        notes.append("a rival club's offer strengthens our hand: tell the buyer")

    def maybe_leak(self, snapshot, intents, notes):
        """Use the press to put pressure on a counterpart who has stopped moving late in the day."""
        if self.leaks >= 2 or snapshot["round"] < self.rounds - 4 or any(i["type"] == "leak" for i in intents):
            return
        for other, neg in snapshot["negotiations"].items():
            theirs = [o["amount"] for o in neg["offers"] if o["by"] == other]
            if neg["agreed"] is None and len(theirs) >= 2 and theirs[-1] == theirs[-2]:
                self.leaks += 1
                intents.append({"to": self.reporter, "kind": neg["kind"], "type": "leak", "terms": None, "text": "",
                                "tone": "anonymous insider tip",
                                "purpose": f"make {other} feel public pressure to move, because the deal may "
                                           f"collapse before the deadline"})
                notes.append(f"{other} has stopped moving: leak to add pressure")
                return

    # ---- steps 4 and 5: write and filter ----------------------------------------------------------------

    @staticmethod
    def amounts(terms):
        if not terms:
            return set()
        values = {terms.get("fee"), terms.get("add_ons"), terms.get("salary")} - {None, 0}
        if terms.get("add_ons"):
            values.add(round(terms["fee"] + terms["add_ons"], 2))
        return values

    def problem(self, text, intent, public_amounts):
        """Why a message is not allowed, or None if it is fine.

        A message may only name the amounts of our own action (and a rival club's public bid). In particular
        it may not repeat the other side's price, which the model tends to present as if it were ours.
        It also may not say the opposite of what was decided.
        """
        if not text.strip():
            return "empty"
        lowered = text.lower().replace(",", ".")
        allowed = self.amounts(intent["terms"]) | (public_amounts if intent["type"] != "accept" else set())
        money_words = re.findall(r"(\d+(?:\.\d+)?)\s*(?:m\b|million|mil\b)", lowered)
        if any(float(n) not in allowed for n in money_words):
            return "states an amount that is not part of the plan"
        agrees = re.search(r"\b(we accept|we agree|i accept|i agree|happy to accept|it'?s a deal|deal is done)\b",
                           lowered)
        refuses = re.search(r"\b(not enough|too much|too low|too high|cannot accept|can't accept|reject|turn down)\b",
                            lowered)
        if intent["type"] in ("offer", "message") and agrees:
            return "sounds like an acceptance, but nothing was accepted"
        if intent["type"] in ("accept", "release") and refuses:
            return "sounds like a refusal, but the decision was to agree"
        return None

    def template(self, intent):
        if intent["type"] == "accept":
            return f"We accept: {intent['text']}."
        if intent["type"] == "offer":
            return f"Our offer: {intent['text']}."
        if intent["type"] == "leak":
            return "Talks are tense and another twist is possible before the deadline."
        if intent["type"] == "release":
            return f"We are triggering the {intent['text']} and will pay it in full."
        if "decided to stay" in intent.get("purpose", ""):
            return "Thank you for your interest, but he has decided to stay at his club."
        return "That is still too much for us." if self.pays else "That is not enough for us."

    def write(self, snapshot, intents, hour):
        """Ask the LLM for the wording; fall back to a template for any message the filter rejects."""
        raws, rejected = [], []
        messages = [None] * len(intents)
        their_amounts = set()
        if snapshot["rival"]["bid"]:
            their_amounts.add(snapshot["rival"]["bid"])      # a rival's bid is public, so it may be mentioned
        if self.use_llm and intents:
            hours_left = self.rounds - 1 - snapshot["round"]
            clock = ("This is the final hour of the transfer window: after this, nothing can be signed until the "
                     "next window." if hours_left == 0 else
                     f"Only {hours_left} hour{'s' if hours_left > 1 else ''} left before the window closes."
                     if hours_left <= 3 else f"{hours_left} hours left before the window closes.")
            lines = [f"It is {hour} on transfer deadline day, the last day of the window. {clock}",
                     "Let the deadline show in your wording: time is almost gone." if hours_left <= 3 else
                     "Do not mention the clock in every message; there are still hours to go.",
                     "", "Actions:"]
            for i, intent in enumerate(intents, 1):
                topic = {"fee": "transfer fee", "salary": "personal terms",
                         "stay": "new contract to stay at his current club"}[intent["kind"]]
                decision = {"offer": f"OFFER {intent['text']}", "accept": f"ACCEPT {intent['text']}",
                            "message": "NO NEW OFFER", "leak": "SECRET TIP TO THE PRESS",
                            "release": f"TRIGGER THE {intent['text'].upper()}"}[intent["type"]]
                lines.append(f"{i}. A message to {intent['to']} about the {topic}. Decision: {decision}. "
                             f"Purpose: {intent['purpose']}. Tone: {intent['tone']}.")
            recent = [f'{e["from"]}: "{e["message"]}"' for e in snapshot["inbox"][-3:] if e["message"]]
            if recent:
                lines += ["", "Their latest messages:"] + recent
            if self.sent:
                lines += ["", "Your earlier messages (do not reuse this wording):"] + self.sent[-3:]
            for attempt in range(2):
                reply, raw = chat_json(self.write_prompt, "\n".join(lines),
                                       seed=None if self.seed is None else self.seed + 10 * snapshot["round"] + attempt)
                raws.append(raw)
                texts = reply.get("messages") if isinstance(reply, dict) else None
                if not isinstance(texts, list):
                    continue
                for i, intent in enumerate(intents):
                    if messages[i] is None and i < len(texts):
                        why = self.problem(str(texts[i]), intent, their_amounts)
                        if why:
                            rejected.append({"message": str(texts[i]), "why": why})
                        else:
                            messages[i] = str(texts[i]).strip()
                if all(messages):
                    break
        messages = [m or self.template(i) for m, i in zip(messages, intents)]
        self.sent += [f'"{m}"' for m in messages]
        return messages, raws, rejected

    # ---- one turn ----------------------------------------------------------------------------------------

    def act(self, view, snapshot):
        hour = f"{self.start_hour + snapshot['round']}:00"
        raws = self.read(snapshot)
        intents, notes = self.plan(snapshot)
        self.maybe_leak(snapshot, intents, notes)
        messages, write_raws, rejected = self.write(snapshot, intents, hour)
        actions = [{"to": i["to"], "type": i["type"], "terms": i["terms"], "message": m,
                    "amount": (i["terms"] or {}).get("fee", (i["terms"] or {}).get("salary"))}
                   for i, m in zip(intents, messages)]
        decision = {"private_reasoning": " | ".join(notes) or "nothing left to negotiate",
                    "actions": actions, "intents": intents, "rejected_messages": rejected,
                    "beliefs": self.believed}
        return decision, raws + write_raws
