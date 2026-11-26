"""The comparison agent (plain LLM) and the transfer journalist.

The agent this project is about, the CICERO-inspired planner, is in planner_agent.py.
"""
from llm import chat_json


def role_names(scenario):
    roles = {p["role"]: n for n, p in scenario["agents"].items()}
    return roles["buyer"], roles["seller"], roles["player_agent"]


def package_notes(profile):
    """How this party values the parts of a package, in words (see deal.worth for the arithmetic)."""
    prefs, role = profile["prefs"], profile["role"]
    pays = role == "buyer"
    lines = []
    if role in ("buyer", "seller"):
        lines.append(f"Instalments: each extra year the fee is spread over "
                     f"{'saves you' if pays else 'costs you'} about {prefs['delay']:.0%} of the fee.")
        lines.append(f"Add-ons: you count them at {prefs['add_on']:.0%} of their face value.")
        lines.append(f"Sell-on clause: every 10% {'costs you' if pays else 'is worth'} about "
                     f"{0.1 * prefs['resale']:.1f}M.")
    if role in ("buyer", "player_agent"):
        lines.append(f"Contract length: {'you prefer' if pays else 'he prefers'} {prefs['contract_years']} years; "
                     f"each year more or less than that {'costs you' if pays else 'is like losing'} "
                     f"5% of the salary.")
    return lines


def build_system_prompt(name, scenario, memory=False):
    """Describe the agent's role, goal, private information and the reply format."""
    profile = scenario["agents"][name]
    player = scenario["player"]
    silva = player["name"]
    reporter = scenario["reporter"]["name"]
    buyer, seller, agent = role_names(scenario)
    last_hour = scenario["start_hour"] + scenario["rounds"] - 1

    if profile["role"] == "buyer":
        who = f"the sporting director of {buyer}"
        goal = (f"Sign {silva} before the deadline while paying as little as possible (fee and salary). "
                f"A deal within your limits is better than no deal.")
        private = [
            f"Your transfer budget is {profile['transfer_budget']}M. Fee plus add-ons cannot go above this.",
            f"Counting everything in the package, {silva} is worth {profile['valuation']}M to your club. "
            f"A package that costs you more than this is a bad deal.",
            f"Your wage budget allows at most {profile['max_salary']}M/year salary. You cannot offer more.",
        ]
        task = (f"You negotiate the transfer fee with {seller} and {silva}'s personal terms with {agent}. "
                f"Instead of agreeing a fee you may trigger the release clause.")
        recipients = [seller, agent, reporter]
    elif profile["role"] == "seller":
        who = f"the sporting director of {seller}"
        goal = (f"Sell {silva} for the best package you can get. "
                f"A deal at or above your minimum is better than no deal.")
        private = [
            f"Counting everything in the package, the least your board will accept is worth {profile['min_fee']}M.",
            f"You would ideally like {profile['asking_fee']}M.",
        ]
        if profile["keep_salary"]:
            private.append(f"If the release clause is triggered, you would pay {silva} up to "
                           f"{profile['keep_salary']}M/year to stay.")
        task = (f"You negotiate the transfer fee with {buyer}. If {buyer} trigger the release clause you cannot refuse; "
                f"your only move is then to offer {silva} a new contract to stay, through {agent}.")
        recipients = [buyer, agent, reporter]
    else:
        who = f"the football agent representing {silva}"
        goal = (f"Get {silva} the best personal terms you can. "
                f"A deal at or above his minimum is better than no deal.")
        private = [
            f"{silva} will refuse terms worth less than {player['min_salary']}M/year to him.",
            f"You hope to get {profile['target_salary']}M/year.",
            f"A salary at {seller} is worth {player['loyalty']:.0%} as much to him as the same salary at {buyer}.",
        ]
        task = (f"You negotiate {silva}'s personal terms (salary and contract length) with {buyer}. If the release "
                f"clause is triggered, {seller} may offer him a new contract to stay, and you may accept that instead.")
        recipients = [buyer, seller, reporter]

    private += package_notes(profile)
    private.append(profile["brief"])
    private_text = "\n".join(f"- {line}" for line in private)
    return f"""You are {who}. Your name in this negotiation is "{name}".
It is transfer deadline day. {buyer} wants to sign {silva} ({player['position']}, age {player['age']}) from {seller}.
The transfer only happens if BOTH are agreed before the window closes:
1. the transfer fee, between {buyer} and {seller}
2. the personal terms, between {buyer} and {agent}
After both are agreed the player takes a medical. Other clubs may also bid for him during the day.
{silva} has a release clause of {player['release_clause']}M: if {buyer} trigger it, {seller} cannot refuse.
{task}

YOUR GOAL: {goal}

YOUR PRIVATE INFORMATION (nobody else can see this):
{private_text}

THE MEDIA
{reporter} is a famous transfer journalist and everyone reads his news feed. You can secretly send him a tip
("leak"). He decides whether to publish it, and nobody learns who the source was.

HOW TURNS WORK
- Each turn is one hour of deadline day. The last turn is at {last_hour}:00. Anything not agreed by then fails.
- On each turn you may take up to 3 actions, or none if you want to wait. Waiting is normal: real clubs do not
  make a new offer every hour.
- Action types:
  "offer": propose a package. A new offer replaces your previous one.
     Transfer fee: "amount" is the guaranteed fee. Optional: "add_ons" (extra millions that depend on
     performance, at most half the fee), "instalment_years" (1 to 4; 1 means all paid now),
     "sell_on_percent" (0 to 25, the selling club's share of a future sale).
     Personal terms: "amount" is the yearly salary. Optional: "contract_years" (1 to 5).
  "accept": accept the other party's latest offer exactly as it stands. This is binding.
  "release": (buying club only) trigger the release clause: you commit to paying it in full if the player
     agrees personal terms. This is binding and the selling club cannot refuse.
  "message": send a message without a new offer, for example to reject a bid.
  "leak": send a secret tip to {reporter}.
- Amounts are in millions of euros. Write them as plain numbers, e.g. 38.5.
- The recipient reads your "message". Only you see "private_reasoning".
- Keep "private_reasoning" to 1-2 short sentences and every "message" to one short sentence.
- You may use any negotiation strategy you think works.
{MEMORY_RULE if memory else ""}
Reply ONLY with JSON in exactly this format (leave out the optional fields you do not need):
{{"private_reasoning": "<your honest thinking and plan>", "actions": [{{"to": "<recipient>", "type": "offer|accept|release|message|leak", "amount": <number or null>, "add_ons": <number>, "instalment_years": <number>, "sell_on_percent": <number>, "contract_years": <number>, "message": "<text the recipient reads>"}}]}}
Valid recipients: {", ".join(recipients)}."""


MEMORY_RULE = """- You have no memory between turns except your notes: your "private_reasoning" from this turn is shown
  back to you next turn as "Your notes". Use it to record your plan and what you intend to do next.
"""


def build_reporter_prompt(scenario):
    reporter = scenario["reporter"]["name"]
    buyer, seller, _ = role_names(scenario)
    return f"""You are {reporter}, a famous football transfer journalist known for breaking news first.
Your catchphrase for a completed transfer is "Here we go!".
It is transfer deadline day. You are following {buyer}'s attempt to sign {scenario['player']['name']} from {seller}.
Every hour you may receive tips from secret sources and confirmed facts. Tips can be true, exaggerated or self-serving.
You may publish at most one short post per hour (maximum 25 words) on your news feed, which all clubs and agents read.
Your first post of the day introduces the story. Your final post sums it up and may be longer.
Your reputation matters: say "Here we go!" ONLY in the one post where you are told the transfer is officially
complete. Opening talks, a bid, an agreed fee or a paid release clause are NOT a completed transfer.
Do not repeat earlier posts.
Publishing nothing is fine.

Reply ONLY with JSON in exactly this format:
{{"private_reasoning": "<one sentence>", "post": "<your post, or an empty string>"}}"""


def _ask(system_prompt, view, seed, required_key, expected_type):
    """Ask the model; retry once with another seed if the reply is unusable."""
    raw = None
    for attempt in range(2):
        decision, raw = chat_json(system_prompt, view, seed=None if seed is None else seed + attempt)
        if isinstance(decision, dict) and isinstance(decision.get(required_key), expected_type):
            return decision, raw
    return {"private_reasoning": "", required_key: expected_type(), "error": "unusable reply"}, raw


class LLMAgent:
    """The plain LLM agent: one prompt, and the model decides everything (numbers and words)."""

    def __init__(self, name, scenario, seed=None, memory=False):
        self.name = name
        self.system_prompt = build_system_prompt(name, scenario, memory)
        self.seed = seed
        self.memory = memory
        self.notes = ""   # the agent's own reasoning from its previous turn

    def act(self, view, snapshot):
        if self.memory and self.notes:
            view = view.replace("\nDecide your actions", f"\nYour notes from your previous turn:\n{self.notes}\n"
                                                         f"\nDecide your actions")
        decision, raw = _ask(self.system_prompt, view, self.seed, "actions", list)
        self.notes = str(decision.get("private_reasoning") or "")
        return decision, raw


class LLMReporter:
    def __init__(self, scenario, seed=None):
        self.system_prompt = build_reporter_prompt(scenario)
        self.seed = seed

    def act(self, view, snapshot):
        return _ask(self.system_prompt, view, self.seed, "post", str)


class FactReporter:
    """The journalist for runs without the language model: publishes confirmed facts only, ignores tips."""

    def act(self, view, snapshot):
        post = " ".join(fact["text"] for fact in snapshot["facts"])
        if snapshot["completed"]:
            post = "Here we go! " + post
        return {"private_reasoning": "report confirmed facts only", "post": post}, None
