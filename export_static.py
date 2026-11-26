"""Build the online, replay-only copy of the web app in docs/ (served by GitHub Pages).

    python export_static.py

GitHub Pages cannot run Python or a language model, so this writes every answer the replay needs as a file:
the menus, the list of recorded experiments, each experiment hour by hour, and the results table.
"""
import json
import shutil
from pathlib import Path

import app

HERE = Path(__file__).parent
OUT = HERE / "docs"
FLAG = '<script>window.STATIC_SITE = true;</script>\n'
SITUATIONS = ["default", "bidding_war", "injured", "release_clause"]
LINEUPS = ["planner", "plain", "planner_no_llm", "planner_buyer_vs_plain", "plain_buyer_vs_planner"]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def order(file):
    situation, lineup, seed = file.split("/")
    return SITUATIONS.index(situation), LINEUPS.index(lineup), int(seed[4:-5])


if OUT.exists():
    shutil.rmtree(OUT)
data = OUT / "data"

options = {"options": {k: list(v) for k, v in app.OPTIONS.items()}, "ages": app.OPTIONS["age"],
           "presets": app.PRESETS, "rival_clubs": app.RIVAL_CLUBS, "defaults": app.DEFAULTS,
           "agent_types": app.AGENT_TYPES, "speeds": app.SPEEDS}
write(data / "options.json", options)
write(data / "state.json", app.state())
write(data / "results.json", app.results())

# only the experiments, not test runs made in the web app
saved = sorted((item for item in app.saved() if not item["file"].startswith("web/")), key=lambda i: order(i["file"]))
write(data / "saved.json", saved)
for item in saved:
    first = app.replay(item["file"], 14)["replay"]
    states = []
    for hour in range(first["first_hour"], first["last_hour"] + 1):
        states.append(app.replay(item["file"], hour))
        if states[-1]["status"] == "done":
            break
    write(data / "replay" / item["file"], states)

for page, name in (("ui.html", "index.html"), ("results.html", "results.html")):
    html = (HERE / page).read_text(encoding="utf-8").replace('href="/"', 'href="./"')
    write_to = OUT / name
    write_to.write_text(html.replace("<script>", FLAG + "<script>", 1), encoding="utf-8")
(OUT / ".nojekyll").write_text("", encoding="utf-8")

size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
print(f"wrote {len(saved)} replays to docs/ ({size / 1e6:.1f} MB)")
