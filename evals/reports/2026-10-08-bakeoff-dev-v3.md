# Extraction bake-off: bakeoff-dev-v3

- Date: 2026-10-08 · split: `dev` · receipts: 20 · prompt: `extract_v3` · total spend: $0.0439

| Config | Critical acc | Field acc | Line-item F1 | JSON valid | Injection recall | Injection false alarms | Arithmetic false alarms | Re-read | $/receipt | $/1k | p50 ms | p95 ms | Gates | Pareto |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| haiku-low | 100.0% | 96.5% | 0.89 | 100% | 100% | 0% | 1/20 | 0/20 | $0.00043 | $0.43 | 2232 | 3347 | ✅ | ★ |
| haiku-2nd | 100.0% | 99.0% | 0.90 | 100% | 100% | 0% | 0/20 | 3/20 | $0.00177 | $1.77 | 2444 | 5695 | ✅ |  |

**Chosen (cheapest frontier config passing gates):** haiku-low

- `haiku-low` top errors: [('subtotal', 2), ('igst', 1), ('upi_reference', 1), ('doc_type', 1), ('travel_from', 1)]; failures: 0
- `haiku-2nd` top errors: [('upi_reference', 1), ('payment_method', 1)]; failures: 0
