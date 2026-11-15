# Deadline Day: a Cicero-inspired negotiating agent

A case study for BM40A1601 (Foundations of AI and Machine Learning). AI agents for a buying club, a selling
club and a player's agent negotiate a football transfer on the last day of the window, hour by hour, while a
journalist reports on it.

The agent under study is in `planner_agent.py`. It follows the idea behind Meta's CICERO: the language model
does not decide what to do. Code models the other side and plans the move; the language model only reads the
other side's messages and writes ours; a filter rejects wording that contradicts the plan.

## What you need

- Python 3.11 or later. No extra packages.
- Ollama running, with the model `qwen3:8b` (`ollama pull qwen3:8b`). Only needed for runs that use the
  language model; everything marked "no model" below works without it.

## How to run it

All commands are run from this folder.

| What | Command |
|---|---|
| The web app (opens http://localhost:8000) | `python app.py` |
| One negotiation in the terminal | `python run.py --think` |
| The same without the language model | `python run.py --no-llm` |
| The checks (no model) | `python test_engine.py` |
| One experiment: a line-up of agents over several seeds | `python evaluate.py run planner --seeds 1-6` |
| The table of all experiment results | `python evaluate.py report` |
| Continue the full set of experiments | `resume_experiments.bat` |

In the web app you choose the player and the situation, start the negotiation and watch it hour by hour.
"Behind the scenes" under each hour shows what every agent believed, planned and wrote. Finished negotiations
can be replayed, and "See the experiment results" shows the comparison charts.

## Online replay

The `docs/` folder is a replay-only copy of the web app, served by GitHub Pages. It needs no Python and no
language model: it replays the 108 negotiations recorded for the experiments, hour by hour, and shows the
results page. Rebuild it after new experiments with `python export_static.py`.

## The files

| File | What it is |
|---|---|
| `planner_agent.py` | The agent: read, believe, plan, write, filter |
| `agents.py` | The two comparison agents (plain language model, rule-based) and the journalist |
| `market.py` | The engine: runs the hours, enforces the rules, rival clubs, release clause, medical |
| `deal.py` | What a deal consists of and what it is worth to each party |
| `scenario_builder.py` | Turns the player and the situation into each party's private numbers |
| `scenario.py` | The default scenario |
| `llm.py` | The connection to the language model through Ollama |
| `run.py` | Runs one negotiation in the terminal and saves its log |
| `evaluate.py` | Runs experiments and prints the results table |
| `app.py`, `ui.html`, `results.html` | The web app |
| `export_static.py` | Builds the replay-only online copy in `docs/` |
| `test_engine.py` | Checks for the engine and the planner |
| `logs/` | Saved negotiations: `logs/<situation>/<line-up>/seed<N>.json` for experiments, `logs/web/` for the web app |

## Sources

- Meta FAIR Diplomacy Team (2022). Human-level play in the game of Diplomacy by combining language models
  with strategic reasoning. *Science*, 378(6624). The idea of separating planning from language.
- Faratin, Sierra and Jennings (1998). Negotiation decision functions for autonomous agents. *Robotics and
  Autonomous Systems*, 24(3-4), 159-182. The rule-based comparison agent.
- The language model is Qwen3 (8 billion parameters), run locally with Ollama.
