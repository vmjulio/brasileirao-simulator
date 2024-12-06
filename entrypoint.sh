#!/bin/bash

python3 brasileirao_simulator/entrypoints/backfill.py;
python3 brasileirao_simulator/entrypoints/results_history_dataset.py;
python3 brasileirao_simulator/entrypoints/matches_history_dataset.py;
#python3 brasileirao_simulator/entrypoints/current_probabilities.py;
