# Extraction bake-off: bakeoff-dev-haiku-v2

- Date: 2026-10-08 · split: `dev` · receipts: 20 · prompt: `extract_v2` · total spend: $0.0104

| Config | Critical acc | Field acc | Line-item F1 | JSON valid | Injection recall | $/receipt | $/1k | p50 ms | p95 ms | Gates | Pareto |
|---|---|---|---|---|---|---|---|---|---|---|---|
| haiku-low | 100.0% | 96.0% | 0.93 | 100% | 100% | $0.00052 | $0.52 | 2459 | 3398 | ✅ | ★ |

**Chosen (cheapest frontier config passing gates):** haiku-low

- `haiku-low` top errors: [('subtotal', 2), ('travel_from', 2), ('travel_to', 2), ('upi_reference', 1), ('doc_type', 1)]; failures: 0
