"""Run one deadline-day negotiation in the terminal and save a full log.

    python run.py                                   # planner agents with the language model
    python run.py --think                           # also show each agent's private thinking
    python run.py --no-llm                          # planner agents, template wording (instant)
    python run.py --plain buyer                     # the plain LLM agent as the buyer
    python run.py --rule seller player_agent        # rule-based seller and player's agent
    python run.py --scenario bidding_war --seed 4   # another situation, another seed
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from agents import RuleReporter
from llm import MODEL
from market import Market
from scenario_builder import DEFAULTS, build_scenario

ROLES = ["buyer", "seller", "player_agent"]
FEATURES = ["memory", "checks"]

# The situations used in the experiments: the choices that differ from the defaults in scenario_builder.py.
SCENARIOS = {
    "default": {"rivals": "One other club is interested"},
    "bidding_war": {"rivals": "Several clubs are bidding", "seller_stance": "Open to offers"},
    "injured": {"player_name": "Rafael Costa", "position": "Defender", "age": 27, "performance": "Key player",
                "contract": "3 years left", "injuries": "Serious injury last season"},
    # A club that will not sell, a buyer that badly needs the player, and a release clause it can afford.
    "release_clause": {"release_clause": "60M", "buyer_need": "Desperate (injury crisis)",
                       "seller_stance": "Does not want to sell", "player_attitude": "Happy where he is"},
}


def run_once(scenario_name="default", seed=1, plain_roles=(), rule_roles=(), features=(), use_llm=True,
             verbose=True, think=False, log_file=None):
    """Run one negotiation, write its log and return (market, log file, seconds).

    Every seat is played by the planner agent unless it is listed in plain_roles (plain LLM) or rule_roles.
    use_llm=False gives the planner template wording and replaces the journalist by a rule, so no model is needed.
    """
    choices = {**DEFAULTS, **SCENARIOS[scenario_name]}
    scenario, facts = build_scenario(choices)
    planner_roles = [role for role in ROLES if role not in plain_roles and role not in rule_roles]
    all_rule = len(rule_roles) == len(ROLES)
    market = Market(scenario, "rule" if all_rule else "llm", seed=seed, verbose=verbose, show_thinking=think,
                    rule_roles=rule_roles, features=features, planner_roles=planner_roles)
    if not use_llm:
        market.journalist = RuleReporter()
        for agent in market.agents.values():
            if hasattr(agent, "use_llm"):
                agent.use_llm = False
    started = time.time()
    market.run()
    seconds = round(time.time() - started, 1)

    if log_file is None:
        log_file = Path(__file__).parent / "logs" / f"{scenario_name}_seed{seed}_{datetime.now():%Y%m%d_%H%M%S}.json"
    seats = {role: "rule" if role in rule_roles else "plain" if role in plain_roles else "planner" for role in ROLES}
    save_log(log_file, market, facts, choices, seats, seconds, scenario_name=scenario_name, seed=seed,
             features=sorted(features), use_llm=use_llm, model=MODEL if use_llm and not all_rule else None)
    return market, log_file, seconds


def save_log(log_file, market, facts, choices, seats, seconds, **details):
    """Write everything about one finished negotiation to a file. The web interface can replay it later."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log = {**details, "seats": seats, "seconds": seconds, "choices": choices, "facts": facts,
           "scenario": market.scenario, "outcome": market.outcome, "news_feed": market.feed,
           "events": market.events, "rival_history": market.rival_history, "leaks": market.leaks,
           "transcript": market.transcript, "turns": market.turns}
    log_file.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def add_run_options(parser):
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="default")
    parser.add_argument("--plain", nargs="*", choices=ROLES, default=[], help="roles played by the plain LLM agent")
    parser.add_argument("--rule", nargs="*", choices=ROLES, default=[], help="roles played by the rule agent")
    parser.add_argument("--features", nargs="*", choices=FEATURES, default=[],
                        help="optional upgrades for the plain LLM agent")
    parser.add_argument("--no-llm", action="store_true", help="planner with template wording; no model needed")


def main():
    os.system("")   # turns on colour support in the Windows terminal
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    add_run_options(parser)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--think", action="store_true", help="show each agent's private thinking")
    parser.add_argument("--quiet", action="store_true", help="only show the summary")
    args = parser.parse_args()
    if args.no_llm and args.plain:
        parser.error("--plain needs the language model, so it cannot be combined with --no-llm")

    try:
        market, log_file, seconds = run_once(args.scenario, args.seed, args.plain, args.rule, args.features,
                                             use_llm=not args.no_llm, verbose=not args.quiet, think=args.think)
    except RuntimeError as problem:
        sys.exit(f"\n{problem}")
    print("\n" + "-" * 100)
    print(market.summary())
    print(f"Time: {seconds}s   Log: {log_file.relative_to(Path(__file__).parent)}")


if __name__ == "__main__":
    main()
