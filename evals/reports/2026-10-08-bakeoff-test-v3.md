# Extraction bake-off: bakeoff-test-v3

- Date: 2026-10-08 · split: `test` · receipts: 80 · prompt: `extract_v3` · total spend: $0.2331

| Config | Critical acc | Field acc | Line-item F1 | JSON valid | Injection recall | Injection false alarms | Arithmetic false alarms | Re-read | $/receipt | $/1k | p50 ms | p95 ms | Gates | Pareto |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| haiku-low | 98.3% | 95.3% | 0.93 | 100% | 100% | 0% | 8/80 | 0/80 | $0.00046 | $0.46 | 2176 | 3358 | ✅ | ★ |
| haiku-2nd | 99.0% | 95.4% | 0.93 | 100% | 100% | 0% | 1/80 | 17/80 | $0.00246 | $2.46 | 2111 | 6648 | ✅ | ★ |

**Chosen (cheapest frontier config passing gates):** haiku-low

- `haiku-low` top errors: [('travel_from', 10), ('travel_to', 10), ('merchant_gstin', 3), ('subtotal', 3), ('doc_type', 3)]; failures: 0
- `haiku-2nd` top errors: [('travel_from', 10), ('travel_to', 10), ('subtotal', 4), ('payment_method', 4), ('doc_type', 3)]; failures: 0
