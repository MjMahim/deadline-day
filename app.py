"""Web interface for the transfer negotiation.

    python app.py          then open http://localhost:8000

Choose the player and the situation from the menus, start the negotiation and watch a short account of each
hour. "Behind the scenes" under every hour shows what each agent believed, planned and what the LLM wrote.
Finished negotiations are saved and can be replayed hour by hour, and /results shows the experiment results.
Uses only the Python standard library.
"""
import json
import random
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from agents import RuleReporter
from deal import describe
from evaluate import summarise
from llm import MODEL
from market import Market, Negotiation, number
from run import SCENARIOS, save_log
from scenario_builder import DEFAULTS, OPTIONS, PRESETS, RIVAL_CLUBS, build_scenario

PORT = 8000
HERE = Path(__file__).parent
LOGS = HERE / "logs"
AGENT_TYPES = {"planner": "Planner + LLM (Cicero-inspired)", "llm": "Plain LLM", "rule": "Rule-based"}
SPEEDS = {"llm": "With the language model (a few minutes)", "instant": "Instant preview (no language model)"}

run = {"id": 0, "status": "idle"}        # the current or latest live negotiation
lock = threading.Lock()


def start(choices):
    scenario, facts = build_scenario(choices)
    seats = {role: choices.get(f"agent_{role}", "planner") for role in ("buyer", "seller", "player_agent")}
    instant = choices.get("speed") == "instant"
    seed = random.randint(1, 9999)
    if instant:                           # no model: plain LLM seats fall back to the planner
        seats = {role: "rule" if kind == "rule" else "planner" for role, kind in seats.items()}
    all_rule = all(kind == "rule" for kind in seats.values())
    market = Market(scenario, "rule" if all_rule else "llm", seed=seed, verbose=False,
                    rule_roles=[r for r, k in seats.items() if k == "rule"],
                    planner_roles=[r for r, k in seats.items() if k == "planner"])
    if instant:
        market.journalist = RuleReporter()
        for agent in market.agents.values():
            if hasattr(agent, "use_llm"):
                agent.use_llm = False
    with lock:
        run.update(id=run["id"] + 1, status="running", market=market, scenario=scenario, facts=facts,
                   seats=seats, seed=seed, error=None, instant=instant, choices={**DEFAULTS, **choices})

    def work():
        started = time.time()
        try:
            market.run()
            run["status"] = "done"
            save_log(LOGS / "web" / f"run_{datetime.now():%Y%m%d_%H%M%S}.json", market, facts, run["choices"],
                     {role: "plain" if kind == "llm" else kind for role, kind in seats.items()},
                     round(time.time() - started, 1), scenario_name="web", seed=seed, features=[],
                     use_llm=not instant, model=None if instant or all_rule else MODEL)
        except Exception as error:        # most likely: Ollama is not running
            run["error"] = f"{type(error).__name__}: {error}"
            run["status"] = "error"
            traceback.print_exc()

    threading.Thread(target=work, daemon=True).start()


# ---- replaying a saved negotiation -------------------------------------------------------------------------

def hour_number(hour):
    return int(hour.split(":")[0])


class Replay:
    """A finished negotiation rebuilt from its log, as it stood at a given hour. It offers the same
    attributes as a live Market, so the same code can describe both."""

    def __init__(self, log, upto):
        self.scenario = scenario = log["scenario"]
        self.profiles = scenario["agents"]
        self.buyer, self.seller, self.agent = (
            next(n for n, p in self.profiles.items() if p["role"] == role)
            for role in ("buyer", "seller", "player_agent"))
        self.reporter = scenario["reporter"]["name"]
        self.role = {name: p["role"] for name, p in self.profiles.items()}
        self.role[self.reporter] = "reporter"
        self.short = {name: p["short"] for name, p in self.profiles.items()}
        self.short[self.reporter] = scenario["reporter"]["short"]

        def cut(items):
            return [item for item in items if hour_number(item["hour"]) <= upto]

        self.transcript, self.turns = cut(log["transcript"]), cut(log["turns"])
        self.events, self.feed = cut(log.get("events", [])), cut(log["news_feed"])
        self.last_hour = max(hour_number(item["hour"]) for item in log["turns"] + log.get("events", []))
        self.outcome = log["outcome"] if upto >= self.last_hour else None

        self.fee = Negotiation("fee", self.buyer, self.seller)
        self.salary = Negotiation("salary", self.buyer, self.agent)
        self.stay = Negotiation("salary", self.seller, self.agent, topic="stay")
        self.negotiations = {frozenset(n.parties): n for n in (self.fee, self.salary, self.stay)}
        self.clause_paid = False
        for entry in self.transcript:
            neg = self.negotiations.get(frozenset((entry["from"], entry["to"])))
            if not entry["valid"] or neg is None:
                continue
            if entry["type"] == "offer":
                neg.standing[entry["from"]] = entry
            elif entry["type"] in ("accept", "release"):
                neg.agreed = entry["terms"]
                self.clause_paid = self.clause_paid or entry["type"] == "release"
        if self.outcome and self.outcome.get("fee_terms"):
            self.fee.agreed = self.outcome["fee_terms"]        # a medical concern may have restructured it

        latest = {}
        for item in cut(log.get("rival_history", [])):
            latest[item["name"]] = item
        self.rivals = [SimpleNamespace(club=club, name=club["name"], bid=latest.get(club["name"], {}).get("bid"),
                                       out=latest.get(club["name"], {}).get("out", False))
                       for club in scenario["rivals"].get("clubs", [])]


def log_path(name):
    """A saved log by its path relative to the logs folder; refuses anything outside that folder."""
    path = (LOGS / name).resolve()
    path.relative_to(LOGS.resolve())
    return path


def replay(name, upto):
    log = json.loads(log_path(name).read_text(encoding="utf-8"))
    market = Replay(log, upto)
    seats = {role: "llm" if kind == "plain" else kind for role, kind in log["seats"].items()}
    choices = log.get("choices") or {**DEFAULTS, **SCENARIOS.get(log.get("scenario_name"), {})}
    done = market.outcome is not None
    return state({"id": name, "status": "done" if done else "running", "market": market,
                  "scenario": log["scenario"], "facts": log["facts"], "seats": seats, "seed": log.get("seed"),
                  "error": None, "instant": not log.get("use_llm", True), "choices": choices,
                  "replay": {"file": name, "hour": upto, "last_hour": market.last_hour,
                             "first_hour": log["scenario"]["start_hour"]}})


def saved():
    """The saved negotiations, newest first, each with a one-line description."""
    items = []
    for path in sorted(LOGS.rglob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:150]:
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
            o, kinds = log["outcome"], set(log["seats"].values())
            who = {"planner": "planner agents", "plain": "plain LLM agents", "rule": "rule agents"}.get(
                next(iter(kinds)), "") if len(kinds) == 1 else "mixed agents"
            if "rival_history" not in log:        # saved by an older version: cannot be replayed faithfully
                continue
            result = {"completed": f"deal at {o['hour']}", "joined rival": "lost to a rival",
                      "stayed (new contract)": "player stayed", "collapsed after medical": "failed medical",
                      "failed (deadline passed)": "no deal", "player refused": "player refused"}[o["result"]]
            where = path.relative_to(LOGS).as_posix()
            items.append({"file": where, "label": f"{log['scenario']['player']['name']}: {result} "
                                                  f"({who}{'' if log.get('use_llm', True) else ', no LLM'}) "
                                                  f"[{where.rsplit('.', 1)[0]}]"})
        except (KeyError, ValueError, StopIteration):
            continue
    return items


def results():
    folders = sorted(p for p in LOGS.glob("*/*") if p.is_dir())
    return [row for row in (summarise(folder) for folder in folders) if row]


# ---- turning the raw record into a short account ---------------------------------------------------------

def tell(market, entry, first, up=False):
    """One short sentence for one action, the way a live blog would put it."""
    who, role = market.short[entry["from"]], market.role[entry["from"]]
    neg = market.negotiations.get(frozenset((entry["from"], entry["to"])))
    if entry["type"] == "leak" or neg is None:
        return None
    text, salary = entry["text"], neg.kind == "salary"
    if entry["type"] == "release":
        return (f"{who} trigger the {text} and are ready to pay it. {market.short[market.seller]} cannot "
                f"refuse: it is up to the player now.")
    if neg.topic == "stay":
        if entry["type"] == "accept":
            return f"The player accepts {market.short[market.seller]}'s new contract: {text}."
        if entry["type"] == "offer" and role == "seller":
            return f"{who} try to keep him with a new contract: {text}."
        return f"{'The player' + chr(39) + 's agent' if role == 'player_agent' else who}: “{entry['message']}”"
    if entry["type"] == "accept":
        return f"Personal terms agreed: {text}." if salary else f"Fee agreed: {text}."
    if entry["type"] == "offer":
        if role == "buyer" and salary:
            return f"{who} offer the player {text}." if first else f"{who} improve their offer to the player: {text}."
        if role == "buyer":
            return f"{who} open with a bid of {text}." if first else f"{who} come back with an improved bid: {text}."
        if role == "seller":
            return f"{who} say no and ask for {text}." if first else f"{who} lower their asking price: {text}."
        return (f"The player's agent asks for {text}." if first else
                f"The player's agent {'raises' if up else 'lowers'} his demand: {text}.")
    if entry["type"] == "message":
        name = "The player's agent" if role == "player_agent" else who
        return f"{name}: “{entry['message']}”"
    return None


def feed(ctx):
    """The journalist's posts, each with the picture that goes with it: a headline and, for the first and the
    last post, the key facts as a small table (taken from the engine, so they are always correct)."""
    market, scenario, facts, choices = ctx["market"], ctx["scenario"], ctx["facts"], ctx["choices"]
    player = scenario["player"]
    outcome = market.outcome if ctx["status"] == "done" else None
    posts = []
    for index, post in enumerate(list(market.feed)):
        about = " ".join(post.get("facts", []))
        picture = {"kind": "update", "headline": "Latest", "rows": [], "note": ""}
        if post.get("final") and outcome:
            if outcome["result"] == "completed":
                fee, terms = outcome["fee_terms"], outcome["salary_terms"]
                rows = [["Release clause paid" if outcome["clause_paid"] else "Guaranteed fee", f"{number(fee['fee'])}M"]]
                if fee["add_ons"]:
                    rows.append(["Add-ons", f"up to {number(fee['add_ons'])}M"])
                rows.append(["Payment", f"over {fee['years']} years" if fee["years"] > 1 else "all up front"])
                if fee["sell_on"]:
                    rows.append(["Sell-on clause", f"{fee['sell_on']}%"])
                rows += [["Salary", f"{number(terms['salary'])}M a year"], ["Contract", f"{terms['years']} years"],
                         ["Medical", {"passed": "passed", "skipped": "deal sheet, no medical",
                                      "concern": "concern, fee restructured"}[outcome["medical"]]]]
                picture = {"kind": "done", "headline": "Here we go!", "rows": rows,
                           "note": f"{player['name']} to {market.buyer}"}
            else:
                headline = {"joined rival": "Hijacked", "stayed (new contract)": "He stays"}.get(
                    outcome["result"], "Deal off")
                reason = outcome["reason"]
                picture = {"kind": "off", "headline": headline, "rows": [],
                           "note": reason[0].upper() + reason[1:] + "."}
        elif index == 0:
            picture = {"kind": "opening", "headline": "Talks open", "note": f"{market.seller} to {market.buyer}?",
                       "rows": [["Player", player["name"]], ["Position", f"{choices['position']}, {player['age']}"],
                                ["This season", choices["performance"]], ["Contract", choices["contract"]],
                                ["Market value", f"about {number(facts['market_value'])}M"],
                                ["Release clause", f"{number(facts['release_clause'])}M"]]}
        elif "release clause" in about:
            picture.update(kind="agreed", headline="Clause triggered")
        elif "drop out" in about:
            picture.update(kind="rival", headline="Rival out")
        elif " bid " in about and any(rival.name in about for rival in market.rivals):
            picture.update(kind="rival", headline="Rival bid")
        elif "agreed" in about:
            picture.update(kind="agreed", headline="Agreement")
        posts.append({"hour": post["hour"], "text": post["text"], "picture": picture})
    return posts


def beliefs(ctx):
    """For each planner agent: what it believed about the other side's secret limit, hour by hour, next to
    the true limit. This is the planner's "model of the other players", the heart of the CICERO idea."""
    market, scenario = ctx["market"], ctx["scenario"]
    series = {}
    for turn in list(market.turns):
        for belief in turn["decision"].get("beliefs", []):
            agent, about, kind = turn["agent"], belief["about"], belief["kind"]
            other = market.profiles[about]
            if other["role"] == "seller":
                truth, what = other["min_fee"], f"the lowest fee {market.short[about]} would accept"
            elif other["role"] == "buyer" and kind == "fee":
                truth, what = other["valuation"], f"the most {market.short[about]} would pay as a fee"
            elif other["role"] == "buyer":
                truth, what = other["max_salary"], f"the highest salary {market.short[about]} would pay"
            else:
                truth, what = scenario["player"]["min_salary"], "the lowest salary the player would accept"
            who = "The player's agent" if market.role[agent] == "player_agent" else market.short[agent]
            item = series.setdefault((agent, about, kind), {
                "agent": agent, "role": market.role[agent], "kind": kind, "truth": truth,
                "title": f"{who} estimating {what}", "points": []})
            item["points"].append({"hour": turn["hour"], "mean": belief["mean"], "low": belief["low"],
                                   "high": belief["high"]})
    return sorted(series.values(), key=lambda item: (item["kind"] != "fee", item["role"] != "buyer"))


def state(ctx=None):
    ctx = ctx or run
    if ctx["status"] == "idle":
        return {"status": "idle"}
    market, scenario, facts = ctx["market"], ctx["scenario"], ctx["facts"]
    hours = {}

    def hour(h):
        return hours.setdefault(h, {"hour": h, "fee": [], "salary": [], "stay": [], "events": [], "news": [],
                                    "thinking": []})

    seen = {}     # (from, to) -> the amount of their previous offer
    for entry in list(market.transcript):
        if not entry["valid"]:
            continue
        neg = market.negotiations.get(frozenset((entry["from"], entry["to"])))
        key = (entry["from"], entry["to"])
        text = tell(market, entry, first=key not in seen,
                    up=entry["type"] == "offer" and key in seen and entry["amount"] > seen[key])
        if entry["type"] == "offer":
            seen[key] = entry["amount"]
        if text and neg:
            hour(entry["hour"])[neg.topic].append({"role": market.role[entry["from"]], "text": text})
    for event in list(market.events):
        if event["public"]:
            hour(event["hour"])["events"].append(event["text"])
    for turn in list(market.turns):
        decision = turn["decision"]
        name = turn["agent"]
        if name == market.reporter and ctx["instant"]:
            continue
        actions = [e for e in market.transcript if e["hour"] == turn["hour"] and e["from"] == name]
        raw = turn["raw"] if isinstance(turn["raw"], list) else [turn["raw"]] if turn["raw"] else []
        hour(turn["hour"])["thinking"].append({
            "agent": name, "role": market.role[name],
            "kind": AGENT_TYPES.get(ctx["seats"].get(market.role[name]), "Journalist (LLM)"),
            "reasoning": [part for part in str(decision.get("private_reasoning", "")).split(" | ") if part],
            "planned": "intents" in decision,
            "intents": [{k: i.get(k) for k in ("to", "type", "text", "purpose", "tone")}
                        for i in decision.get("intents", [])],
            "rejected": decision.get("rejected_messages", []),
            "post": decision.get("post"),
            "actions": [{"to": e["to"], "type": e["type"], "text": e["text"], "message": e["message"],
                         "valid": e["valid"], "note": e["note"]} for e in actions],
            "llm_output": raw,
        })

    buyer, seller = market.profiles[market.buyer], market.profiles[market.seller]
    player = scenario["player"]["name"]

    def last(neg, name):
        offer = neg.standing[name]
        return offer["amount"] if offer else None

    track = {"buyer": last(market.fee, market.buyer), "seller": last(market.fee, market.seller),
             "agreed": market.fee.agreed["fee"] if market.fee.agreed else None, "market": facts["market_value"],
             "salary_buyer": last(market.salary, market.buyer), "salary_agent": last(market.salary, market.agent),
             "salary_agreed": market.salary.agreed["salary"] if market.salary.agreed else None,
             "wage": facts["market_wage"],
             "rivals": [{**rival.club, "bid": rival.bid, "out": rival.out} for rival in market.rivals],
             "clause": facts["release_clause"], "clause_paid": market.clause_paid}

    result = None
    if ctx["status"] == "done":
        o = market.outcome
        if o["result"] == "completed":
            winner = ("The selling club did not want to sell at that price, but the clause left it no choice."
                      if o["clause_paid"] and o["seller_sold_below_minimum"] else
                      "The buying club paid more than he was worth to them." if o["overpaid"] else
                      "The selling club sold for less than its own minimum." if o["seller_sold_below_minimum"] else
                      "No price could have suited both clubs, yet they agreed." if o["buyer_surplus_share"] is None else
                      "The buying club got the better of the deal." if o["buyer_surplus_share"] > 0.6 else
                      "The selling club got the better of the deal." if o["buyer_surplus_share"] < 0.4 else
                      "The two clubs split the difference fairly evenly.")
            medical = {"passed": "He passed his medical.", "skipped": "There was no time for a full medical.",
                       "concern": "The medical raised a concern, so part of the fee was turned into add-ons."
                       }.get(o["medical"], "")
            fee_text = (f"the release clause of {number(o['fee'])}M, which {market.seller} could not refuse"
                        if o["clause_paid"] else describe("fee", o["fee_terms"]))
            result = {"headline": f"Done deal: {player} joins {market.buyer}", "ok": True,
                      "text": f"Agreed at {o['hour']}. Fee: {fee_text}. "
                              f"Personal terms: {describe('salary', o['salary_terms'])}. {medical} {winner}",
                      "tiles": [["Guaranteed fee", f"{number(o['fee'])}M"],
                                ["Market value", f"{number(facts['market_value'])}M"],
                                ["Salary", f"{number(o['salary'])}M a year"],
                                ["Contract", f"{o['salary_terms']['years']} years"]]}
        elif o["result"] == "joined rival":
            result = {"headline": f"Hijacked: {player} joins {o['rival_club']} instead", "ok": False,
                      "text": f"{market.buyer} could not agree a deal in time, and {market.seller} accepted "
                              f"{o['rival_club']}'s bid of {number(o['rival_fee'])}M.", "tiles": []}
        elif o["result"] == "stayed (new contract)":
            result = {"headline": f"He stays: {player} signs a new contract at {market.seller}", "ok": False,
                      "text": f"{market.buyer} triggered the release clause, so {market.seller} could not block "
                              f"the move. But the player chose to stay: {describe('salary', o['stay_terms'])}.",
                      "tiles": []}
        elif o["result"] == "collapsed after medical":
            result = {"headline": f"Deal off: the medical ends {player}'s move", "ok": False,
                      "text": "Everything was agreed, but the deal fell through at the medical.", "tiles": []}
        elif o["result"] == "player refused":
            result = {"headline": f"Deal off: {player} turns the move down", "ok": False,
                      "text": "The clubs agreed a fee, but the personal terms were below what he would accept.",
                      "tiles": []}
        else:
            open_parts = [label for neg, label in ((market.fee, "the fee"), (market.salary, "personal terms"))
                          if neg.agreed is None]
            result = {"headline": f"No deal: the window closes and {player} stays at {market.seller}", "ok": False,
                      "text": f"Still not agreed at the deadline: {' and '.join(open_parts)}.", "tiles": []}

    agent_name = market.agent
    return {
        "feed": feed(ctx), "beliefs": beliefs(ctx), "replay": ctx.get("replay"),
        "id": ctx["id"], "status": ctx["status"], "error": ctx["error"], "seed": ctx["seed"],
        "facts": facts, "buyer": market.buyer, "seller": market.seller, "player": player, "track": track,
        "seats": {role: AGENT_TYPES[kind] for role, kind in ctx["seats"].items()},
        "private": [
            {"who": market.buyer, "role": "buyer", "lines": [
                f"Worth to them: {number(buyer['valuation'])}M", f"Transfer budget: {number(buyer['transfer_budget'])}M",
                f"Highest salary: {number(buyer['max_salary'])}M a year",
                f"Preferred contract: {buyer['prefs']['contract_years']} years"]},
            {"who": market.seller, "role": "seller", "lines": [
                f"Lowest package they accept: worth {number(seller['min_fee'])}M", f"Asking price: {number(seller['asking_fee'])}M",
                f"Counts add-ons at {seller['prefs']['add_on']:.0%} of face value",
                (f"Would pay him up to {number(seller['keep_salary'])}M a year to stay" if seller["keep_salary"]
                 else "Will not try to keep him")]},
            {"who": agent_name, "role": "player_agent", "lines": [
                f"Lowest salary he accepts: {number(scenario['player']['min_salary'])}M a year",
                f"Hoping for: {number(market.profiles[agent_name]['target_salary'])}M a year",
                f"Preferred contract: {market.profiles[agent_name]['prefs']['contract_years']} years",
                f"A salary at his own club is worth {scenario['player']['loyalty']:.0%} of one elsewhere"]},
        ],
        "hours": [hours[h] for h in sorted(hours, key=hour_number)],
        "result": result,
    }


# ---- web server ------------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def send(self, body, kind="application/json", code=200):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                self.send((HERE / "ui.html").read_bytes(), "text/html")
            elif url.path == "/results":
                self.send((HERE / "results.html").read_bytes(), "text/html")
            elif url.path == "/options":
                self.send({"options": {k: list(v) for k, v in OPTIONS.items()}, "ages": OPTIONS["age"],
                           "presets": PRESETS, "rival_clubs": RIVAL_CLUBS,
                           "defaults": DEFAULTS, "agent_types": AGENT_TYPES, "speeds": SPEEDS})
            elif url.path == "/state":
                self.send(state())
            elif url.path == "/saved":
                self.send(saved())
            elif url.path == "/replay":
                self.send(replay(query["file"], int(query["hour"])))
            elif url.path == "/results.json":
                self.send(results())
            else:
                self.send({"error": "not found"}, code=404)
        except Exception as error:
            self.send({"error": f"{type(error).__name__}: {error}"}, code=400)

    def do_POST(self):
        try:
            if self.path == "/preview":           # the public figures for the choices made so far
                return self.send(build_scenario(self.body())[1])
            if self.path != "/start":
                return self.send({"error": "not found"}, code=404)
            if run["status"] == "running":
                return self.send({"error": "A negotiation is already running."}, code=409)
            start(self.body())
        except Exception as error:
            return self.send({"error": f"{type(error).__name__}: {error}"}, code=400)
        self.send({"ok": True})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Transfer negotiation running at http://localhost:{PORT}  (Ctrl+C to stop)")
    threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
