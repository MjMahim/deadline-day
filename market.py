"""Deadline-day negotiation engine: runs the hours, enforces the rules and records everything.

One hour of deadline day is one turn. In each hour:
    1. other clubs may bid for the player (the "rivals": named clubs that follow a simple script, not agents)
    2. the buying club, the selling club and the player's agent each act
    3. the journalist decides what to publish
When both the fee and the personal terms are agreed, the player takes a medical. If nothing is agreed by the
last hour, the player joins the rival club with the best acceptable bid, and otherwise stays where he is.
"""
import random
import re
import unicodedata

from agents import LLMAgent, LLMReporter, RuleAgent, RuleReporter
from deal import describe, headline, money, normalise, worth
from planner_agent import PlannerAgent

MAX_ACTIONS = 3
LABELS = {"fee": "Transfer fee", "salary": "Personal terms", "stay": "New contract to stay"}
MEDIA_WORDS = ("journalist", "reporter", "press", "media")
MEDICAL_CUT = 0.15      # after a medical concern, this share of the fee is moved into appearance add-ons

COLORS = {"buyer": "\033[94m", "seller": "\033[91m", "player_agent": "\033[92m", "reporter": "\033[95m"}
YELLOW, DIM, BOLD, RESET = "\033[93m", "\033[2m", "\033[1m", "\033[0m"


class Negotiation:
    """A two-party negotiation over one package: the transfer fee or the personal terms."""

    def __init__(self, kind, party_a, party_b, topic=None):
        self.kind = kind                 # which kind of package: "fee" or "salary"
        self.topic = topic or kind       # what the talks are about: "fee", "salary" or "stay"
        self.parties = (party_a, party_b)
        self.latest = None   # most recent offer from either side: {"by", "amount", "terms", "hour"}
        self.standing = {party_a: None, party_b: None}   # each party's own latest offer, still open to accept
        self.agreed = None   # the agreed package
        self.offers = []

    def other(self, name):
        a, b = self.parties
        return b if name == a else a

    def status(self, viewer):
        if self.agreed is not None:
            return f"AGREED: {describe(self.kind, self.agreed)}"
        other = self.other(viewer)
        mine, theirs = self.standing[viewer], self.standing[other]
        if mine is None and theirs is None:
            return "no offers yet"
        parts = [f"latest offer from {other}: {describe(self.kind, theirs['terms'])}" if theirs
                 else f"no offer from {other} yet",
                 f"your latest offer: {describe(self.kind, mine['terms'])}" if mine
                 else "you have not made an offer yet"]
        return "; ".join(parts)


class Rival:
    """Another club in the race. It follows a simple script, not an agent: it bids at some point, raises when
    it is outbid, and drops out when the price passes what it is willing to pay."""

    def __init__(self, club, scenario, level, rng):
        self.club = club
        self.name = club["name"]
        self.rng = rng
        self.bid = None        # its current bid to the selling club, all paid up front
        self.out = False
        value, wage = scenario["market"]["value"], scenario["market"]["wage"]
        self.first = rng.randint(1, 5) if level == 1 else rng.randint(0, 4)        # the hour of its first bid
        self.most = value * (rng.uniform(0.85, 1.05) if level == 1 else rng.uniform(0.9, 1.25))
        self.most = min(self.most, scenario["player"]["release_clause"])           # nobody pays more than the clause
        self.salary = money(max(wage * rng.uniform(1.0, 1.3),                      # what it offers the player
                                scenario["player"]["min_salary"]))

    def step(self, round_index, to_beat):
        """`to_beat`: the best offer the selling club has from anyone else, in its own terms.
        Returns "bid", "raise", "out" or None."""
        if self.out or round_index < self.first:
            return None
        if self.bid is None:
            if to_beat >= self.most:
                self.out = True          # already too expensive: it never enters the race
                return None
            self.bid = money(max(self.most * self.rng.uniform(0.85, 0.95), min(self.most, to_beat * 1.04)))
            return "bid"
        if to_beat > self.bid:
            if to_beat >= self.most:
                self.out, self.bid = True, None
                return "out"
            if self.rng.random() < 0.6:
                self.bid = money(min(self.most, to_beat * 1.04))
                return "raise"
        return None


def parse_amount(value):
    """Accept 38.5, "38.5" or "38.5M"; return None for anything else."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 2)
    if isinstance(value, str):
        match = re.search(r"\d+(\.\d+)?", value.replace(",", "."))
        if match:
            return round(float(match.group()), 2)
    return None


def plain(text):
    """Make LLM text safe for any terminal: straight quotes, plain dashes, no accents."""
    text = str(text)
    for fancy, simple in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                          ("–", "-"), ("—", "-"), ("…", "..."), ("€", "EUR ")):
        text = text.replace(fancy, simple)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def number(x):
    return f"{x:g}"


class Market:
    def __init__(self, scenario, mode, seed=None, verbose=True, show_thinking=False, rule_roles=(), features=(),
                 planner_roles=()):
        """mode: "llm" or "rule". rule_roles / planner_roles: roles played by the rule agent or by the
        CICERO-inspired planner agent instead of the plain LLM agent (cross-play).
        features: optional upgrades for the plain LLM agent, any of "memory" and "checks"."""
        self.scenario = scenario
        self.features = set(features)
        self.verbose = verbose
        self.show_thinking = show_thinking
        self.profiles = scenario["agents"]
        self.buyer, self.seller, self.agent = (
            next(n for n, p in self.profiles.items() if p["role"] == role)
            for role in ("buyer", "seller", "player_agent"))
        self.reporter = scenario["reporter"]["name"]
        self.order = [self.buyer, self.seller, self.agent]
        self.role = {name: p["role"] for name, p in self.profiles.items()}
        self.role[self.reporter] = "reporter"
        self.short = {name: p["short"] for name, p in self.profiles.items()}
        self.short[self.reporter] = scenario["reporter"]["short"]
        self.width = max(len(s) for s in self.short.values())

        self.fee = Negotiation("fee", self.buyer, self.seller)
        self.salary = Negotiation("salary", self.buyer, self.agent)
        # If the release clause is paid, the selling club's only move is to offer the player a new contract.
        self.stay = Negotiation("salary", self.seller, self.agent, topic="stay")
        self.clause_paid = False
        self.clause_hour = None      # when the clause was paid
        self.negotiations = {frozenset(n.parties): n for n in (self.fee, self.salary, self.stay)}
        self.rng = random.Random(seed)
        level = scenario["rivals"]["level"]
        self.rivals = [Rival(club, scenario, level, random.Random(f"rival-{seed}-{club['name']}"))
                       for club in scenario["rivals"]["clubs"]]

        self.agents = {}
        for name in self.order:
            if mode == "rule" or self.role[name] in rule_roles:
                self.agents[name] = RuleAgent(name, scenario)
            elif self.role[name] in planner_roles:
                self.agents[name] = PlannerAgent(name, scenario, seed)
            else:
                self.agents[name] = LLMAgent(name, scenario, seed, memory="memory" in self.features)
        self.journalist = LLMReporter(scenario, seed) if mode == "llm" else RuleReporter()

        self.transcript = []   # every action, valid or not
        self.turns = []        # every decision, including private reasoning
        self.leaks = []        # secret tips sent to the journalist
        self.facts = []        # confirmed events the journalist learns about
        self.events = []       # things that happen outside the negotiation: rival bids, the medical
        self.feed = []         # the journalist's published posts
        self.rival_history = []   # each rival's bid hour by hour, for replaying a run later
        self.corrected_posts = 0  # posts replaced because they stated something that is not a known fact
        self.early_here_we_go = 0
        self.outcome = None

    # ---- helpers -------------------------------------------------------------------------------------

    def open_talks(self, name):
        """The negotiations this party takes part in. Talks about staying only open once the clause is paid."""
        return [neg for neg in self.negotiations.values()
                if name in neg.parties and (neg.topic != "stay" or self.clause_paid)]

    def top_rival(self):
        """The rival club with the highest bid on the table, or None."""
        bidders = [rival for rival in self.rivals if rival.bid]
        return max(bidders, key=lambda rival: rival.bid) if bidders else None

    def counterparts(self, name):
        return [neg.other(name) for neg in self.open_talks(name)]

    def worth(self, name, kind, terms):
        return worth(self.profiles[name], kind, terms)

    @staticmethod
    def matches(option, text):
        option = option.lower()
        return bool(text) and (text in option or option in text or
                               any(len(word) > 3 and word in text for word in option.split()))

    def resolve_recipient(self, name, to, kind):
        """Map the model's free-text recipient (e.g. "Riverside") to an exact name."""
        text = str(to or "").lower()
        if kind == "leak" or self.matches(self.reporter, text) or any(w in text for w in MEDIA_WORDS):
            return self.reporter
        options = self.counterparts(name)
        match = next((option for option in options if self.matches(option, text)), None)
        if match or len(options) > 1:
            return match
        # One counterpart only: a vague recipient goes to them, but not a message meant for someone else.
        strangers = [other for other in self.order if other != name and other not in options]
        if any(self.matches(stranger, text) for stranger in strangers):
            return None
        return options[0]

    def hard_problem(self, name, kind, terms):
        """Why the rules forbid this party from agreeing to this package, or None."""
        profile = self.profiles[name]
        if profile["role"] != "buyer":
            return None
        if kind == "fee" and terms["fee"] + terms["add_ons"] > profile["transfer_budget"]:
            return f"fee plus add-ons is above your transfer budget of {number(profile['transfer_budget'])}M"
        if kind == "salary" and terms["salary"] > profile["max_salary"]:
            return f"the salary is above your wage limit of {number(profile['max_salary'])}M a year"
        return None

    def reservation(self, name, kind):
        """The private value an agent should not go past: the worst deal that is still worth taking."""
        profile = self.profiles[name]
        if profile["role"] == "buyer":
            return profile["valuation"] if kind == "fee" else profile["max_salary"]
        if profile["role"] == "seller":
            top = self.top_rival()
            return max(profile["min_fee"], top.bid if top else 0)
        return self.scenario["player"]["min_salary"]

    # ---- console output ------------------------------------------------------------------------------

    def paint(self, name, pad=True):
        return f"{COLORS[self.role[name]]}{self.short[name]:<{self.width if pad else 0}}{RESET}"

    def print_action(self, entry):
        if not self.verbose:
            return
        message = f'"{plain(entry["message"])}"' if entry["message"] else ""
        flag = f"  {YELLOW}INVALID: {entry['note']}{RESET}" if not entry["valid"] else ""
        receiver = self.paint(entry["to"]) if entry["to"] in self.role else f"{entry['to']:<{self.width}}"
        print(f"{entry['hour']:>5}  {self.paint(entry['from'])} > {receiver}  "
              f"{entry['type'].upper():<7} {BOLD}{entry['text']}{RESET}  {message}{flag}")

    def print_thinking(self, name, decision):
        if self.verbose and self.show_thinking and decision.get("private_reasoning"):
            print(f"{DIM}{'':7}{self.short[name]} thinks: {plain(decision['private_reasoning'])}{RESET}")
        if self.verbose and decision.get("error"):
            print(f"{YELLOW}{'':7}{self.short[name]}: {decision['error']}{RESET}")

    def happen(self, hour, text, public=True):
        """Record something that happened outside the negotiation."""
        self.events.append({"hour": hour, "text": text, "public": public})
        if public:
            self.facts.append({"hour": hour, "text": text})
        if self.verbose:
            print(f"{hour:>5}  {YELLOW}{'EVENT':<{self.width}}{RESET}   {text}")

    # ---- what each agent sees ------------------------------------------------------------------------

    def snapshot(self, name, round_index):
        role = self.role[name]
        top = self.top_rival()
        # Rival bids are public (the press reports them); the salary on offer is known only to the player's side.
        rival = {"name": top.name if top else None, "bid": top.bid if top else None,
                 "salary": top.salary if top and role == "player_agent" else None}
        return {
            "round": round_index,
            "negotiations": {neg.other(name): {"kind": neg.kind, "topic": neg.topic, "latest": neg.latest,
                                               "agreed": neg.agreed, "theirs": neg.standing[neg.other(name)],
                                               "mine": neg.standing[name], "offers": list(neg.offers)}
                             for neg in self.open_talks(name)},
            "clause": {"amount": self.scenario["player"]["release_clause"], "paid": self.clause_paid},
            "inbox": [e for e in self.transcript if e["to"] == name and e["valid"]],
            "rival": rival,
        }

    def view(self, name, round_index, hour):
        rounds = self.scenario["rounds"]
        lines = [f"It is now {hour} on deadline day. This is turn {round_index + 1} of {rounds}."]
        if round_index == rounds - 1:
            lines.append("THIS IS THE LAST TURN. Anything not agreed now fails.")
        lines.append("\nDeal status:")
        for neg in self.open_talks(name):
            lines.append(f"- {LABELS[neg.topic]} with {neg.other(name)}: {neg.status(name)}")
        clause = self.scenario["player"]["release_clause"]
        if self.clause_paid:
            lines.append(f"- {self.buyer} have triggered the release clause of {number(clause)}M and will pay it "
                         f"if the player agrees, so {self.seller} cannot stop the move. It now depends only on "
                         f"the player's personal terms. {self.seller} may offer him a new contract to stay.")
        else:
            lines.append(f"- Release clause: {number(clause)}M. If {self.buyer} pay it in full, {self.seller} "
                         f"cannot refuse and the move depends only on the player.")
        role, bidders = self.role[name], [rival for rival in self.rivals if rival.bid]
        if bidders:
            bids = ", ".join(f"{rival.name} {number(rival.bid)}M" for rival in bidders)
            lines.append(f"\nOther clubs bidding for him (all paid up front): {bids}.")
            top = self.top_rival()
            if role == "seller":
                lines.append(f"If this deal fails you can sell to {top.name} at the deadline.")
            elif role == "player_agent":
                lines.append(f"{top.name} offer him {number(top.salary)}M a year.")
            else:
                lines.append(f"{self.seller} will not accept less than the best of these from you.")
        if "checks" in self.features:
            lines.append("\nSituation check (computed for you, always correct):")
            lines += self.checks(name, rounds - round_index - 1)
        lines.append(f"\nNews feed of {self.reporter}:")
        lines += [f"[{post['hour']}] {post['text']}" for post in self.feed[-5:]] or ["(no posts yet)"]
        lines.append("\nConversation so far:")
        history = [e for e in self.transcript if name in (e["from"], e["to"]) and (e["valid"] or e["from"] == name)]
        if not history:
            lines.append("(nothing yet)")
        for e in history:
            sender = "You" if e["from"] == name else e["from"]
            receiver = "you" if e["to"] == name else e["to"]
            what = f" {e['text']}" if e["text"] else ""
            note = "" if e["valid"] else f"  [REJECTED BY SYSTEM: {e['note']}]"
            lines.append(f"[{e['hour']}] {sender} -> {receiver}: {e['type'].upper()}{what}. \"{e['message']}\"{note}")
        lines.append("\nDecide your actions for this turn.")
        return "\n".join(lines)

    def checks(self, name, turns_left):
        """Plain-language arithmetic the LLM tends to get wrong: how each open offer compares with its limits."""
        pays = self.role[name] == "buyer"
        lines = []
        for neg in self.open_talks(name):
            if neg.agreed is not None or neg.topic == "stay":
                continue
            other, limit = neg.other(name), self.reservation(name, neg.kind)
            mine, theirs = neg.standing[name], neg.standing[other]
            lines.append(f"- {LABELS[neg.topic]}: counting everything in the package, the "
                         f"{'most you should pay' if pays else 'least you should accept'} is worth "
                         f"{number(limit)}M to you.")
            if theirs:
                value = round(self.worth(name, neg.kind, theirs["terms"]), 2)
                problem = self.hard_problem(name, neg.kind, theirs["terms"])
                good = value <= limit if pays else value >= limit
                verdict = (f"not allowed ({problem})" if problem else
                           "a good deal for you" if good else "a bad deal for you")
                lines.append(f"  {other}'s offer {'costs you' if pays else 'is worth'} {number(value)}M in your "
                             f"terms: accepting it is {verdict}.")
            if mine:
                lines.append(f"  Your own offer {'costs you' if pays else 'is worth'} "
                             f"{number(round(self.worth(name, neg.kind, mine['terms']), 2))}M in your terms.")
        if lines:
            lines.append(f"- Turns left after this one: {turns_left}. Anything still open at the deadline "
                         f"means no transfer.")
        else:
            lines.append("- Everything you negotiate is agreed. You have nothing left to decide.")
        return lines

    def reporter_view(self, hour, tips, facts, completed, final=False):
        hours_left = self.scenario["start_hour"] + self.scenario["rounds"] - 1 - int(hour.split(":")[0])
        lines = [f"It is now {hour} on deadline day, the last day of the transfer window. "
                 + (f"{hours_left} hours until the window closes." if hours_left > 1 else
                    "One hour until the window closes." if hours_left == 1 else "The window closes this hour.")]
        if not self.feed:
            lines.append("This is your FIRST post of the day: introduce the story (who wants to sign whom).")
        if final:
            lines.append("This is your FINAL post of the day and may be up to 60 words. If the transfer is complete, "
                         "give every detail of the deal: fee, add-ons, instalments, sell-on clause, salary and "
                         "contract length. If it is off, say exactly why.")
        if self.clause_paid and not completed:
            lines.append("Careful with the release clause: it has been TRIGGERED (the buying club is ready to pay "
                         "it), but no money is paid unless the transfer goes through. Never write that it was paid.")
        lines.append("\nTips from secret sources this hour:")
        lines += [f'- from {tip["from"]}: "{tip["message"]}"' for tip in tips] or ["(none)"]
        lines.append("\nConfirmed facts this hour:")
        lines += [f"- {fact['text']}" for fact in facts] or ["(none)"]
        if completed:
            lines.append("- THE TRANSFER IS NOW OFFICIALLY COMPLETE.")
        lines.append("\nYour earlier posts:")
        lines += [f"[{post['hour']}] {post['text']}" for post in self.feed] or ["(none)"]
        lines.append("\nDecide whether to publish a post.")
        return "\n".join(lines)

    # ---- applying actions ----------------------------------------------------------------------------

    def apply(self, name, action, hour):
        kind = str(action.get("type", "")).lower().strip()
        if "counter" in kind or kind == "bid":
            kind = "offer"
        elif kind in ("reject", "decline", "wait", "none"):
            kind = "message"
        elif kind == "tip":
            kind = "leak"
        elif "clause" in kind or "trigger" in kind:
            kind = "release"
        message = str(action.get("message") or "").strip()

        to = self.resolve_recipient(name, action.get("to"), kind)
        if to == self.reporter:
            kind = "leak"
        if kind in ("message", "leak") and not message:
            return

        entry = {"hour": hour, "from": name, "to": to or str(action.get("to")), "type": kind,
                 "amount": None, "terms": None, "text": "", "message": message, "valid": True, "note": ""}

        def invalid(note):
            entry["valid"], entry["note"] = False, note

        neg = self.negotiations.get(frozenset((name, to))) if to else None
        theirs = neg.standing[neg.other(name)] if neg else None   # the other side's open offer

        def agree():
            neg.agreed = entry["terms"] = theirs["terms"]
            entry["amount"], entry["text"] = theirs["amount"], describe(neg.kind, theirs["terms"])
            self.facts.append({"hour": hour, "text": f"{LABELS[neg.topic]} agreed between {name} and "
                                                     f"{neg.other(name)}: {entry['text']}."})

        # In the hour the clause is paid, the player first hears what his own club has to say.
        must_wait = name == self.buyer and neg is self.salary and self.clause_hour == hour
        closing = kind == "accept" or (kind == "offer" and theirs is not None)

        if kind == "leak":
            self.leaks.append({"hour": hour, "from": name, "message": message})
        elif must_wait and closing and self._would_close(neg, kind, action, theirs):
            invalid("the player wants to hear from his own club before he decides; try again next hour")
        elif neg is None or neg not in self.open_talks(name):
            neg = None
            invalid(f"you can only contact {', '.join(self.counterparts(name) + [self.reporter])}")
        elif kind == "release":
            clause = self.scenario["player"]["release_clause"]
            if name != self.buyer or neg is not self.fee:
                invalid("only the buying club can pay the release clause, to the selling club")
            elif neg.agreed is not None:
                invalid("already agreed")
            elif clause > self.profiles[name]["transfer_budget"]:
                invalid(f"the release clause of {number(clause)}M is above your transfer budget")
            else:
                terms = normalise("fee", clause)
                neg.agreed = entry["terms"] = terms
                entry["amount"], entry["text"] = clause, f"release clause of {number(clause)}M"
                self.clause_paid, self.clause_hour = True, hour      # "paid" = triggered; money moves on completion
                self.happen(hour, f"{self.buyer} trigger {self.scenario['player']['name']}'s release clause and "
                                  f"are ready to pay the {number(clause)}M. {self.seller} cannot block the move: "
                                  f"it is now up to the player.")
        elif kind == "offer":
            given = action.get("terms") or {
                "add_ons": action.get("add_ons"), "sell_on": action.get("sell_on_percent"),
                "years": action.get("instalment_years") if neg.kind == "fee" else action.get("contract_years")}
            try:
                terms = normalise(neg.kind, parse_amount(action.get("amount")), given)
            except ValueError as problem:
                terms = None
                invalid(str(problem))
            if terms is None:
                pass
            elif neg.agreed is not None:
                invalid("already agreed")
            elif self.hard_problem(name, neg.kind, terms):
                invalid(self.hard_problem(name, neg.kind, terms))
            elif theirs and terms == theirs["terms"]:
                entry["type"] = "accept"      # offering exactly what the other side asked for closes the deal
                agree()
            else:
                entry["amount"], entry["terms"] = headline(neg.kind, terms), terms
                entry["text"] = describe(neg.kind, terms)
                neg.latest = neg.standing[name] = {"by": name, "amount": entry["amount"], "terms": terms,
                                                   "hour": hour}
                neg.offers.append(dict(neg.latest))
        elif kind == "accept":
            stated = parse_amount(action.get("amount"))
            if neg.agreed is not None:
                invalid("already agreed")
            elif theirs is None:
                invalid("there is no offer from the other side to accept")
            elif stated is not None and stated != theirs["amount"]:
                invalid(f"you can only accept their latest offer, which is {describe(neg.kind, theirs['terms'])}")
            elif self.hard_problem(name, neg.kind, theirs["terms"]):
                invalid(self.hard_problem(name, neg.kind, theirs["terms"]))
            else:
                agree()
        elif kind != "message":
            invalid(f"unknown action type '{kind}'")

        self.transcript.append(entry)
        self.print_action(entry)

    @staticmethod
    def _would_close(neg, kind, action, theirs):
        """Whether this action would seal the deal: an accept, or an offer identical to the other side's."""
        if kind == "accept":
            return True
        try:
            given = action.get("terms") or {"years": action.get("contract_years")}
            return normalise(neg.kind, parse_amount(action.get("amount")), given) == theirs["terms"]
        except ValueError:
            return False

    # ---- things that happen around the negotiation -----------------------------------------------------

    def rival_turn(self, round_index, hour):
        """Each rival club reacts to the best offer the selling club has from anyone else."""
        if self.fee.agreed is not None:
            return
        buyer_best = max((self.worth(self.seller, "fee", o["terms"]) for o in self.fee.offers
                          if o["by"] == self.buyer), default=0)
        player = self.scenario["player"]["name"]
        for rival in self.rivals:
            others = max((other.bid for other in self.rivals if other is not rival and other.bid), default=0)
            what = rival.step(round_index, max(buyer_best, others))
            if what == "bid":
                self.happen(hour, f"{rival.name} bid {number(rival.bid)}M for {player}.")
            elif what == "raise":
                self.happen(hour, f"{rival.name} raise their bid for {player} to {number(rival.bid)}M.")
            elif what == "out":
                self.happen(hour, f"{rival.name} drop out of the race for {player}.")
        self.rival_history += [{"hour": hour, "name": rival.name, "bid": rival.bid, "out": rival.out}
                               for rival in self.rivals]

    def medical(self, hour):
        """The player's medical at the buying club. Returns "passed", "concern", "collapsed" or "failed"."""
        odds, roll = self.scenario["medical"], self.rng.random()
        player = self.scenario["player"]["name"]
        if roll < odds["pass"]:
            self.happen(hour, f"{player} passes his medical.")
            return "passed"
        if roll >= odds["pass"] + odds["concern"]:
            self.happen(hour, f"{player} fails his medical.")
            return "failed"
        # A concern: the buyer insists that part of the fee depends on the player actually playing.
        terms = self.fee.agreed
        cut = money(terms["fee"] * MEDICAL_CUT)
        revised = {**terms, "fee": terms["fee"] - cut, "add_ons": terms["add_ons"] + cut}
        if self.clause_paid:      # the clause fixes the fee, so the buyer can only go ahead or walk away
            self.happen(hour, f"The medical raises a concern, but with the release clause already triggered "
                              f"{self.buyer} go ahead.")
            return "concern"
        if self.worth(self.seller, "fee", revised) >= self.reservation(self.seller, "fee"):
            self.fee.agreed = revised
            self.happen(hour, f"The medical raises a concern. The clubs restructure the fee: "
                              f"{describe('fee', revised)}.")
            return "concern"
        self.happen(hour, f"The medical raises a concern and {self.seller} refuse to lower the guaranteed fee.")
        return "collapsed"

    def check_done(self, hour, round_index):
        player = self.scenario["player"]
        if self.stay.agreed is not None:      # he accepted his club's new contract
            self.outcome = {"hour": hour, "round": round_index + 1, "medical": None,
                            "result": "stayed (new contract)", "stay_terms": self.stay.agreed,
                            "reason": f"{player['name']} chose to stay at {self.seller} and signed a new "
                                      f"contract: {describe('salary', self.stay.agreed)}"}
            self.happen(hour, f"{player['name']} turns down {self.buyer} and signs a new contract at "
                              f"{self.seller}: {describe('salary', self.stay.agreed)}.")
            return True
        if self.fee.agreed is None or self.salary.agreed is None:
            return False
        self.outcome = {"hour": hour, "round": round_index + 1, "medical": None}
        if self.worth(self.agent, "salary", self.salary.agreed) < player["min_salary"]:
            self.outcome["result"] = "player refused"
            self.outcome["reason"] = "the personal terms were below what the player was willing to accept"
            self.happen(hour, f"{player['name']} rejects the personal terms and the move to {self.buyer} is off.")
            return True
        if round_index == self.scenario["rounds"] - 1:
            self.happen(hour, "There is no time for a full medical: the clubs file a deal sheet before the deadline.")
            self.outcome["medical"] = "skipped"
        else:
            self.outcome["medical"] = self.medical(hour)
        if self.outcome["medical"] in ("failed", "collapsed"):
            self.outcome["result"] = "collapsed after medical"
            self.outcome["reason"] = ("the player failed his medical" if self.outcome["medical"] == "failed" else
                                      "the medical raised a concern and the selling club refused to accept a "
                                      "lower guaranteed fee")
            return True
        self.outcome["result"] = "completed"
        self.happen(hour, f"{player['name']} joins {self.buyer}: {describe('fee', self.fee.agreed)}; "
                          f"{describe('salary', self.salary.agreed)}.")
        return True

    MONEY = r"(\d+(?:\.\d+)?)\s*(?:m\b|m/|million|mil\b)"

    def grounded(self, post, tips):
        """Whether every amount of money and every age in a post can be traced to a confirmed fact, a tip he
        received this hour, or the player's public details. Language models like to invent such figures."""
        player = self.scenario["player"]
        sources = " ".join([fact["text"] for fact in self.facts] + [tip["message"] for tip in tips]).lower()
        known = {float(n) for n in re.findall(self.MONEY, sources.replace(",", "."))}
        known |= {float(player["release_clause"]), float(self.scenario["market"]["value"])}
        text = post.lower().replace(",", ".")
        if any(float(n) not in known for n in re.findall(self.MONEY, text)):
            return False
        return all(int(age) == player["age"] for age in re.findall(r"(\d+)[- ]year[- ]old", text))

    def reporter_turn(self, hour, completed=False, final=False):
        tips = [leak for leak in self.leaks if leak["hour"] == hour]
        facts = [fact for fact in self.facts if fact["hour"] == hour]
        if not tips and not facts:
            return
        snapshot = {"tips": tips, "facts": facts, "completed": completed}
        decision, raw = self.journalist.act(self.reporter_view(hour, tips, facts, completed, final), snapshot)
        self.turns.append({"hour": hour, "agent": self.reporter, "decision": decision, "raw": raw})
        self.print_thinking(self.reporter, decision)
        post = str(decision.get("post") or "").strip()
        if (self.clause_paid and not completed and "clause" in post.lower()
                and re.search(r"\b(paid|pays|paying|payment)\b", post.lower())):
            post = ""      # the clause is triggered, not paid, until the deal is done: use the confirmed facts
        if not post and facts and (final or not self.feed or self.clause_paid):
            post = " ".join(fact["text"] for fact in facts)
        if post and not self.grounded(post, tips):
            self.corrected_posts += 1      # he made up a figure: publish the confirmed facts instead
            post = ""
            if not facts:
                return
            post = " ".join(fact["text"] for fact in facts)
        if not post and (final or not self.feed):      # the day always opens and closes with a post
            post = " ".join(fact["text"] for fact in facts)
        if not post or any(post == earlier["text"] for earlier in self.feed):
            return
        if "here we go" in post.lower() and not completed:
            # The catchphrase is reserved for a finished transfer. Count the slip and take the phrase out,
            # so the public feed never announces a deal that has not happened.
            self.early_here_we_go += 1
            post = re.sub(r"\s*here we go\s*[!.]*", " ", post, flags=re.IGNORECASE).strip()
            if not post:
                post = " ".join(fact["text"] for fact in facts)
            if not post or any(post == earlier["text"] for earlier in self.feed):
                return
        self.feed.append({"hour": hour, "text": post, "final": final, "facts": [fact["text"] for fact in facts]})
        if self.verbose:
            print(f"{hour:>5}  {self.paint(self.reporter)}   {BOLD}NEWS{RESET}  {COLORS['reporter']}{plain(post)}{RESET}")

    # ---- main loop -----------------------------------------------------------------------------------

    def run(self):
        start, rounds = self.scenario["start_hour"], self.scenario["rounds"]
        self.happen(f"{start}:00", f"{self.buyer} open talks with {self.seller} to sign "
                                   f"{self.scenario['player']['name']} before the window closes.")
        for r in range(rounds):
            hour = f"{start + r}:00"
            if self.verbose and r:
                print()
            self.rival_turn(r, hour)
            for name in self.order:
                decision, raw = self.agents[name].act(self.view(name, r, hour), self.snapshot(name, r))
                self.turns.append({"hour": hour, "agent": name, "decision": decision, "raw": raw})
                self.print_thinking(name, decision)
                for action in decision.get("actions", [])[:MAX_ACTIONS]:
                    if isinstance(action, dict):
                        self.apply(name, action, hour)
                    if self.check_done(hour, r):
                        self.reporter_turn(hour, completed=self.outcome["result"] == "completed", final=True)
                        return self.finish()
            if r == rounds - 1:
                self.deadline(hour)
            self.reporter_turn(hour, final=r == rounds - 1)
        return self.finish()

    def sticking_points(self):
        """In words, what was still not agreed and how far apart the sides were."""
        points = []
        for neg, label, asker in ((self.fee, "the fee", "asking price"), (self.salary, "personal terms", "demand")):
            if neg.agreed is not None:
                continue
            bid, ask = neg.standing[self.buyer], neg.standing[neg.other(self.buyer)]
            if bid and ask:
                points.append(f"{label} ({self.short[self.buyer]}'s last offer was {describe(neg.kind, bid['terms'])}, "
                              f"the {asker} was {describe(neg.kind, ask['terms'])})")
            elif bid or ask:
                points.append(f"{label} (only one side ever made an offer)")
            else:
                points.append(f"{label} (talks never started)")
        return " and ".join(points)

    def deadline(self, hour):
        """The window closes without a deal: the player goes to the best rival bidder if it can agree, or stays."""
        player, rival = self.scenario["player"], self.top_rival()
        open_points = self.sticking_points()
        self.outcome = {"hour": None, "round": None, "medical": None, "result": "failed (deadline passed)",
                        "reason": f"time ran out with no agreement on {open_points}"}
        if (rival and rival.bid >= self.profiles[self.seller]["min_fee"]
                and rival.salary >= player["min_salary"]):
            self.outcome.update(result="joined rival", rival_club=rival.name, rival_fee=rival.bid,
                                rival_salary=rival.salary,
                                reason=f"{self.buyer} could not agree {open_points} in time, so {self.seller} "
                                       f"accepted {rival.name}'s bid of {number(rival.bid)}M")
            self.happen(hour, f"The window closes. {player['name']} joins {rival.name} instead, for "
                              f"{number(rival.bid)}M, because {self.buyer} could not agree {open_points}.")
        else:
            self.happen(hour, f"The window closes with no deal on {open_points}. "
                              f"{player['name']} stays at {self.seller}.")

    def finish(self):
        o = self.outcome
        done = o["result"] == "completed"
        o["fee_terms"], o["salary_terms"] = (self.fee.agreed, self.salary.agreed) if done else (None, None)
        o["clause_paid"] = self.clause_paid
        o["fee"] = self.fee.agreed["fee"] if done else None
        o["salary"] = self.salary.agreed["salary"] if done else None
        o["invalid_actions"] = sum(not e["valid"] for e in self.transcript)
        o["fee_offers"] = len(self.fee.offers)
        o["salary_offers"] = len(self.salary.offers)
        o["leaks"] = len(self.leaks)
        o["news_posts"] = len(self.feed)
        o["early_here_we_go"] = self.early_here_we_go
        o["corrected_posts"] = self.corrected_posts
        if done:
            buyer, seller = self.profiles[self.buyer], self.profiles[self.seller]
            o["buyer_cost"] = round(self.worth(self.buyer, "fee", self.fee.agreed), 2)
            o["seller_value"] = round(self.worth(self.seller, "fee", self.fee.agreed), 2)
            # 1.0 = the buyer got the player at the seller's minimum, 0.0 = the buyer paid all he was worth to it
            zone = buyer["valuation"] - seller["min_fee"]
            o["buyer_surplus_share"] = round((buyer["valuation"] - o["buyer_cost"]) / zone, 2) if zone > 0 else None
            o["overpaid"] = o["buyer_cost"] > buyer["valuation"] + 0.01
            o["seller_sold_below_minimum"] = o["seller_value"] < seller["min_fee"] - 0.01
        return o

    def summary(self):
        """A short, readable recap of the run."""
        o = self.outcome
        buyer, seller = self.profiles[self.buyer], self.profiles[self.seller]
        lines = []
        if o["result"] != "completed":
            lines.append(f"{BOLD}NO DEAL{RESET} - {o['result']}.")
        else:
            lines.append(f"{BOLD}COMPLETED at {o['hour']}{RESET}  |  {describe('fee', o['fee_terms'])}  |  "
                         f"{describe('salary', o['salary_terms'])}")
            lines.append(f"Cost to the buyer {number(o['buyer_cost'])}M (worth {number(buyer['valuation'])}M to it)  |  "
                         f"value to the seller {number(o['seller_value'])}M (minimum {number(seller['min_fee'])}M)  |  "
                         f"medical: {o['medical']}")
            if o["overpaid"]:
                lines.append(f"{YELLOW}Buyer paid more than the player is worth to them.{RESET}")
            if o["seller_sold_below_minimum"]:
                lines.append(f"{YELLOW}Seller sold below its minimum.{RESET}")
        for neg in (self.fee, self.salary):
            parts = []
            for party in neg.parties:
                amounts = [number(offer["amount"]) for offer in neg.offers if offer["by"] == party]
                parts.append(f"{self.paint(party, pad=False)} {' > '.join(amounts) or '-'}")
            lines.append(f"{LABELS[neg.topic] + ' offers:':<22}" + "    |    ".join(parts))
        lines.append(f"Leaks: {o['leaks']}   News posts: {o['news_posts']}   "
                     f"Early 'Here we go': {o['early_here_we_go']}   Invalid actions: {o['invalid_actions']}")
        return "\n".join(lines)
