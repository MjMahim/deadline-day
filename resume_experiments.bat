@echo off
rem Continues the comparison experiments from where they stopped (finished runs are skipped),
rem then prints the results table. Needs Ollama running. Run it from this folder.
set ALL=buyer seller player_agent
python evaluate.py run planner --seeds 1-6
python evaluate.py run plain --seeds 1-6 --plain %ALL%
python evaluate.py run planner --scenario release_clause --seeds 1-6
python evaluate.py run plain --scenario release_clause --seeds 1-6 --plain %ALL%
python evaluate.py run planner_buyer_vs_plain --seeds 1-6 --plain seller player_agent
python evaluate.py run plain_buyer_vs_planner --seeds 1-6 --plain buyer
python evaluate.py run planner --scenario bidding_war --seeds 1-6
python evaluate.py run plain --scenario bidding_war --seeds 1-6 --plain %ALL%
python evaluate.py run planner --scenario injured --seeds 1-6
python evaluate.py run plain --scenario injured --seeds 1-6 --plain %ALL%
python evaluate.py report
pause
