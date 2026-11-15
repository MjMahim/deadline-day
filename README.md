<div align="center">

# ⚽ Deadline Day

**Strategic negotiation agents in a simulated football transfer market**

*Code decides, a language model talks.*

[![Live replay](https://img.shields.io/badge/▶_Live_replay-mjmahim.github.io%2Fdeadline--day-0f6b3f?style=for-the-badge)](https://mjmahim.github.io/deadline-day/)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![No dependencies](https://img.shields.io/badge/dependencies-standard_library_only-2ea44f)
![LLM](https://img.shields.io/badge/LLM-Qwen3_8B_via_Ollama-000000?logo=ollama&logoColor=white)
![Runs](https://img.shields.io/badge/recorded_negotiations-108-f6c343)
![Course](https://img.shields.io/badge/LUT_University-BM40A1601-e4007c)

<img src="assets/replay.png" alt="The replay site: a completed transfer with the agreed fee, salary and contract" width="900">

</div>

---

## The idea

Language models are now used as agents, yet they negotiate poorly. They break rules, overpay, or fail to agree.
Meta's **CICERO** reached human level in the negotiation game *Diplomacy* by letting a planner decide and a
language model only talk. This project tests that idea with a **small 8-billion-parameter model on a laptop**,
on football's transfer deadline day.

Three AI agents (the **buying club**, the **selling club** and the **player's agent**) negotiate a transfer hour
by hour from 14:00 until the window closes at 23:00. Along the way there are rival bids, release clauses, a
medical and a journalist who reports on every move.

## How the agent works

```mermaid
flowchart LR
    A["📨 Read<br/><sub>LLM: is the other side<br/>firm or flexible?</sub>"] --> B["🧠 Believe<br/><sub>Bayesian estimate of<br/>their secret limit</sub>"]
    B --> C["♟️ Plan<br/><sub>wait, bid, accept<br/>or trigger the clause</sub>"]
    C --> D["✍️ Write<br/><sub>LLM: one sentence<br/>per action</sub>"]
    D --> E["🛡️ Filter<br/><sub>block wording that<br/>breaks the plan</sub>"]
    style B fill:#e3f1e8,stroke:#0f6b3f
    style C fill:#e3f1e8,stroke:#0f6b3f
    style E fill:#e3f1e8,stroke:#0f6b3f
```

Green steps are code; the language model only reads and writes. Every number the agent sends comes from the plan.

<img src="assets/timeline.png" alt="One negotiation: the buyer opens low, holds, outbids a rival by 1M and signs 13.5M below the player's worth" width="760">

*One real run: the buyer opens low, waits while the seller comes down, outbids a rival by just 1M and signs
the player 13.5M below his worth to the club.*

## Results

The planner was compared with a **plain LLM agent** (the same model deciding on its own) and a **rule-based
negotiator** (Faratin et al., 1998), each over 24 negotiations in four situations.

| | Planner + LLM | Plain LLM | Rule-based |
|---|:---:|:---:|:---:|
| Rule-breaking actions per run | **0** | 8.3 | 0 |
| Deals where the buyer overpaid | **0 of 9** | 8 of 11 | 0 of 23 |
| Runs where the clubs failed to agree | **0** | 12 of 24 | 0 |

<img src="assets/outcomes.png" alt="How each run ended and what each deal cost" width="900">

- **The LLM alone failed** to agree in half its runs and overpaid in most deals.
- **The planner reads the situation.** It let rivals have an overpriced player and accepted a happy player's
  choice to stay. The rule-based seller sold at 54.2M while a rival was offering up to 63.5M.
- **The LLM adds communication, not decisions.** Without it, outcomes were the same in three of four
  situations. The filter blocked 80 messages that contradicted the plan.

## See inside the agents

The web app shows, for every hour, what each agent believed, planned, sent and had blocked, next to a
journalist's feed.

<table>
<tr>
<td width="58%"><img src="assets/beliefs.png" alt="Belief charts: each side's estimate of the other's secret limit over the day"></td>
<td width="42%"><img src="assets/feed.png" alt="The hour-by-hour account next to the journalist's feed"></td>
</tr>
<tr>
<td><sub>Each planner's estimate of the other side's secret limit, hour by hour, against the true value.</sub></td>
<td><sub>The hour-by-hour account and the journalist's posts.</sub></td>
</tr>
</table>

## Run it

**Requirements:** Python 3.11+ (no extra packages). For runs with the language model, [Ollama](https://ollama.com)
with `ollama pull qwen3:8b`. Everything marked "no model" works without it.

| What | Command |
|---|---|
| The web app (opens http://localhost:8000) | `python app.py` |
| One negotiation in the terminal | `python run.py --think` |
| The same without the language model | `python run.py --no-llm` |
| The checks (no model) | `python test_engine.py` |
| One experiment: a line-up of agents over several seeds | `python evaluate.py run planner --seeds 1-6` |
| The table of all experiment results | `python evaluate.py report` |
| Continue the full set of experiments | `resume_experiments.bat` |
| Rebuild the online replay in `docs/` | `python export_static.py` |

In the web app you choose the player and the situation, start the negotiation and watch it hour by hour.
"Behind the scenes" under each hour shows what every agent believed, planned and wrote. The
[online copy](https://mjmahim.github.io/deadline-day/) replays the 108 recorded negotiations without needing
Python or a model.

## Project structure

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
| `logs/` | The recorded experiments: `logs/<situation>/<line-up>/seed<N>.json` |

## Authors

**Md Mahim** (agent design and implementation, simulation, experiments) and **Toki Ahamed** (literature
review, analysis of results). Course project for BM40A1601 Foundations of Artificial Intelligence and Machine
Learning, LUT University, 2026.

## References

- Meta Fundamental AI Research Diplomacy Team (FAIR) (2022). Human-level play in the game of Diplomacy by
  combining language models with strategic reasoning. *Science*, 378(6624), 1067–1074.
- Faratin, P., Sierra, C. and Jennings, N. R. (1998). Negotiation decision functions for autonomous agents.
  *Robotics and Autonomous Systems*, 24(3–4), 159–182.
- Yang, A. et al. (2025). Qwen3 technical report. arXiv:2505.09388.
