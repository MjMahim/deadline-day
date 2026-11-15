"""The default scenario used by the command-line runs and the experiments.

It is built from the same real-life factors as the web interface (see scenario_builder.py):
Leo Silva, a 24-year-old striker and key player with two years left on his contract; a selling club that needs
the money; a buying club for which he is a main target; and one other club interested in him.
Everything in an agent's entry is private to that agent. All money is in millions of euros.
"""
from scenario_builder import build_scenario

SCENARIO, FACTS = build_scenario({"rivals": "One other club is interested"})
