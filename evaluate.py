"""Run an experiment (one configuration over several seeds) and compare experiments.

    python evaluate.py run planner --seeds 1-6                           # planner agents in every seat
    python evaluate.py run plain --seeds 1-6 --plain buyer seller player_agent
    python evaluate.py run planner --scenario bidding_war --seeds 1-6
    python evaluate.py report                                            # table of every experiment so far

Logs are kept in logs/<scenario>/<experiment>/seed<N>.json. A seed that already has a log is skipped, so an
interrupted experiment can simply be started again.
"""
import argparse
import json
import sys
from pathlib import Path
from statistics import mean

from run import add_run_options, run_once

LOGS = Path(__file__).parent / "logs"


def parse_seeds(text):
    """"1-6" -> [1..6], "1,3,5" -> [1, 3, 5]."""
    seeds = []
    for part in text.split(","):
        first, _, last = part.partition("-")
        seeds += range(int(first), int(last or first) + 1)
    return seeds


def run(args):
    folder = LOGS / args.scenario / args.name
    for seed in parse_seeds(args.seeds):
        log_file = folder / f"seed{seed}.json"
        if log_file.exists():
            print(f"seed {seed}: already done, skipping", flush=True)
            continue
        market, _, seconds = run_once(args.scenario, seed, args.plain, args.features, verbose=False, log_file=log_file)
        o = market.outcome
        deal = f"fee {o['fee']:g}M, salary {o['salary']:g}M/yr" if o["fee"] is not None else "-"
        print(f"seed {seed}: {o['result']:<26} {deal:<28} invalid {o['invalid_actions']}  "
              f"leaks {o['leaks']}  {seconds}s", flush=True)
    print()
    report([folder])


def summarise(folder):
    """Average the outcome of every run in one experiment folder."""
    logs = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(folder.glob("seed*.json"))]
    if not logs:
        return None
    outcomes = [log["outcome"] for log in logs]
    deals = [o for o in outcomes if o["result"] == "completed"]

    def avg(values, digits=1):
        values = [v for v in values if v is not None]
        return round(mean(values), digits) if values else None

    return {
        "scenario": folder.parent.name,
        "experiment": folder.name,
        "runs": len(logs),
        "deals": len(deals),
        "to_rival": sum(o["result"] == "joined rival" for o in outcomes),
        "fee": avg(o["fee"] for o in deals),
        "buyer_cost": avg(o["buyer_cost"] for o in deals),
        "overpaid": sum(o["overpaid"] for o in deals),
        "below_min": sum(o["seller_sold_below_minimum"] for o in deals),
        "buyer_share": avg((o["buyer_surplus_share"] for o in deals), 2),
        "salary": avg((o["salary"] for o in deals), 2),
        "deal_hour": avg(int(o["hour"].split(":")[0]) for o in deals),
        "offers": avg(o["fee_offers"] + o["salary_offers"] for o in outcomes),
        "invalid": avg(o["invalid_actions"] for o in outcomes),
        "leaks": avg(o["leaks"] for o in outcomes),
        "seconds": avg((log["seconds"] for log in logs), 0),
    }


COLUMNS = [("scenario", "Scenario"), ("experiment", "Experiment"), ("runs", "Runs"), ("deals", "Deals"),
           ("to_rival", "To rival"), ("fee", "Fee"), ("buyer_cost", "Buyer cost"), ("overpaid", "Overpaid"),
           ("below_min", "Below min"), ("buyer_share", "Buyer share"), ("salary", "Salary"),
           ("deal_hour", "Hour"), ("offers", "Offers"), ("invalid", "Invalid"), ("leaks", "Leaks"),
           ("seconds", "Sec/run")]


def report(folders=None):
    if folders is None:
        folders = sorted(p for p in LOGS.glob("*/*") if p.is_dir())
    rows = [row for row in (summarise(f) for f in folders) if row]
    if not rows:
        print("No experiments found.")
        return
    table = [[title for _, title in COLUMNS]]
    table += [["-" if row[key] is None else str(row[key]) for key, _ in COLUMNS] for row in rows]
    widths = [max(len(line[i]) for line in table) for i in range(len(COLUMNS))]
    for n, line in enumerate(table):
        print("  ".join(cell.ljust(w) if i < 2 else cell.rjust(w) for i, (cell, w) in enumerate(zip(line, widths))))
        if n == 0:
            print("  ".join("-" * w for w in widths))
    print("\nFee (guaranteed part), buyer cost (the whole package in the buyer's terms) and salary are averages over\n"
          "completed deals, in millions. Overpaid = deals costing the buyer more than the player is worth to it.\n"
          "Below min = deals worth less to the seller than its minimum. Buyer share: 1 = the buyer got him at the\n"
          "seller's minimum, 0 = it paid all he was worth to it. Offers, invalid actions and leaks are per run.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    r = commands.add_parser("run", help="run one experiment")
    r.add_argument("name", help="experiment name, used as the log folder")
    r.add_argument("--seeds", default="1-6")
    add_run_options(r)
    commands.add_parser("report", help="compare all experiments")
    args = parser.parse_args()
    if args.command == "report":
        report()
    else:
        try:
            run(args)
        except RuntimeError as problem:
            sys.exit(f"\n{problem}")


if __name__ == "__main__":
    main()
