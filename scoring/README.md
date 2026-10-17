# Optional preference scorer

`vpg_score.py` ranks alternatives, records a principal's actual end-review choices and fits a versioned
preference model. It is separate from the graph engine: a score does not approve a human gate,
change graph policy or override a failed rubric. No real preference data is distributed.

From the checkout, use a store outside the source tree:

```sh
python scoring/vpg_score.py --data /absolute/path/to/preferences score FORK.json
python scoring/vpg_score.py --data /absolute/path/to/preferences record FORK.json \
  --rank B,A,C --principal "<reviewer>" --why "<their words>" --source "<review record>"
python scoring/vpg_score.py --data /absolute/path/to/preferences fit
python scoring/vpg_score.py --data /absolute/path/to/preferences report
python -m pytest -q scoring
```

```json
{"fork_id": "F-001", "run": "example", "version": 1, "fork_type": "concept",
 "dims": [{"id": "CON-FIT", "measured": false}],
 "candidates": [
   {"id": "A", "scores": {"CON-FIT": 4}, "passes_rubric": true},
   {"id": "B", "scores": {"CON-FIT": 5}, "passes_rubric": true}
 ]}
```

Types: `concept`, `visual`, `voice_music`, `scene`. Record only an actual principal's choice; use
`--retrospective` for hindsight-biased records so they stay out of calibration metrics.
Models use reproducible ranking-based fits, dimension bias and calibration. Predictions are not
validated preferences until tested on that principal's held-out decisions. Listening remains human.
