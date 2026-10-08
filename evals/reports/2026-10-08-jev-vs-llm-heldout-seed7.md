# System One benchmark: jev-vs-llm-heldout-seed7

- Date: 2026-10-08 · documents: 80 (golden set, ground-truth receipts as input) + 8 personal-expense probes · questions: category (choice, 14 options), alcohol_present (noul), personal_expense (noul) · 'confident' = category confidence ≥ 0.7; the rest are asked of the employee

| Engine | Category acc | Acc when confident (coverage) | Alcohol acc | Alcohol recall | Personal FP | Personal recall | Calibration error (ECE) | p50 ms | p95 ms | $/1k docs | Failures |
|---|---|---|---|---|---|---|---|---|---|---|---|
| jev | 85.0% | 94.4% (89%) | 100.0% | n/a | 12.5% | 100% | 0.085 | 546 | 1013 | $0.040 | 0 |
| llm | 85.0% | 97.0% (82%) | 98.8% | n/a | 10.0% | 100% | 0.087 | 1655 | 2174 | $0.117 | 0 |

- `jev` category errors (12): ['s7-0015: local_conveyance→misc (0.30)', 's7-0021: misc→wfh_supplies (0.61)', 's7-0029: misc→meals (0.34)', 's7-0031: local_conveyance→misc (0.74)', 's7-0036: local_conveyance→misc (0.81)', 's7-0035: client_entertainment→meals (0.98)', 's7-0039: local_conveyance→misc (0.66)', 's7-0055: client_entertainment→meals (0.49)']
- `llm` category errors (12): ['s7-0015: local_conveyance→meals (0.35)', 's7-0021: misc→wfh_supplies (0.70)', 's7-0029: misc→wfh_supplies (0.40)', 's7-0031: local_conveyance→misc (0.20)', 's7-0035: client_entertainment→meals (0.80)', 's7-0036: local_conveyance→meals (0.30)', 's7-0039: local_conveyance→misc (0.20)', 's7-0057: local_conveyance→misc (0.20)']
