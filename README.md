# hack-272a0f3c-bk-group

Hackathon team repository for BK-Group.

## Project and current status

HackAlem AI 2026, Logistics track. Partner: Elektrokomplekt / Электрокомплект (ekt.kz). Case: automatic calculation of supplier orders for warehouse replenishment.

**Status as of 23 September 2026: initial requirements review and exploratory data analysis completed. The application, forecasting engine, and user interface are not implemented yet.** This commit documents actual analysis progress; it is not a finished solution. Analysis scripts have been run locally but are not included in this documentation-only commit. No installation or demo is available yet.

## Business problem

Purchasing managers manually combine Excel reports to decide what to order, how much, and when. Recorded sales can understate demand during stockouts or overstate regular demand after exceptional bulk transactions. Existing stock, reservations, and incoming deliveries must also be considered to avoid both shortages and excess inventory.

## Mandatory case requirements

1. Calculate replenishment by SKU using sales history, stock, incoming shipments, categories, and growth inputs.
2. Account for seasonality and sustained demand growth.
3. Estimate missed demand during stockouts.
4. Identify exceptional large purchases, including customer-level patterns when anonymized customer identifiers are available.
5. Group recommendations by supplier and explain each quantity.

The intended workflow is calculation, manager review/edit, approval, and export. Orders must not be sent to suppliers without explicit employee approval. Minimum order quantities and order multiples are an optional enhancement supported by the supplied files.

## Sources inspected

- [Official task brief](https://docs.google.com/document/d/1Z4faOPlT1t6NMCuJSKKB8-vkZ2LVzGMR0PO9rtZgSnM/edit)
- [IEK archive](https://drive.google.com/file/d/18AqNcbkaqrvoLuX9fPsLMleEj7g6CWFb/view)
- [Systeme Electric archive](https://drive.google.com/file/d/1CkzeElvVm_YwkFCofXX1MGBJuUjMecTz/view)

Each archive contains six Excel workbooks: detailed sales, monthly sales, monthly inventory, seasonality, goods in transit, and minimum quantities/order multiples. The Systeme Electric transit report also includes categories, current stock, reservations, free stock, and growth factors.

The two detailed reports contain 248,917 rows in total. Our initial filter retained 248,467 positive outgoing-invoice lines from 2025 and 2026, representing 80,933 distinct invoice documents. Negative movements, non-sales document types, and incomplete rows were excluded. These are gross positive quantities, not return-adjusted net demand. Historical correction rows also exist for earlier years.

## Analysis method

Analysis used Python, pandas, NumPy, and openpyxl locally. These are analysis tools, not a committed application stack.

- Identify invoices by the full document field, and products by supplier, SKU, and unit.
- Aggregate quantity per invoice/product before classification.
- Never add metres, pieces, and packages together.
- For each product with sufficient observations, flag a large line when quantity exceeds both three times the median and Q3 + 3 * IQR, where IQR = Q3 - Q1.
- An invoice is flagged if at least one assessed product line is large. This is a heuristic for unusual size, not proof of a one-off customer order.
- Missing customer IDs mean repeat customers cannot be identified.

### Initial exploration: all available 2025-2026 transactions

Using separate product/year thresholds and at least 20 observations per product/year:

| Classification | Invoices | Share |
| --- | ---: | ---: |
| At least one unusually large line | 6,674 | 8.25% |
| No flagged lines; all included lines assessable | 62,335 | 77.02% |
| No flagged lines, but incomplete history for some products | 11,924 | 14.73% |
| Total | 80,933 | 100% |

Flagged product lines account for 58.02% of positive quantities measured in pieces, 34.84% in metres, and 32.04% in packages. These are physical-volume shares, not revenue shares. An absence of flags does not prove that purchases are regular.

### Comparable year-over-year analysis

To avoid comparing a full year with an incomplete year, we compared 1 January through 22 September in both 2025 and 2026. We retained 680 supplier/SKU/unit combinations with at least 20 observations in EACH year and used one pooled threshold per product across both periods.

| Metric | 2025 | 2026 |
| --- | ---: | ---: |
| Invoices containing the retained products | 29,169 | 30,401 |
| Invoices with a flagged line among retained products | 2,762 | 2,492 |
| Flagged share of those invoices | 9.47% | 8.20% |

These percentages have a different period, product population, and threshold basis from the initial exploration above.

Key findings:

- 460 of the 506 products with large purchases in 2026 also had large purchases in 2025: 90.91% of products, not customers.
- 157 products had at least five large purchases in EACH comparison period.
- Repeated large quantities often resemble standard batch sizes. They must not automatically be removed as exceptional demand.

| Product | Median purchase, 2025 / 2026 | Large purchases, 2025 / 2026 | Repeated batch sizes |
| --- | --- | --- | --- |
| IEK VA47-29 16A, SKU 010500006_ | 12 / 12 pieces | 376 / 438 | 144, 288, 432 |
| IEK VA47-29 25A, SKU 010500008_ | 12 / 12 pieces | 235 / 297 | 144, 288, 720 |
| Systeme IMT35100, SKU 030200192_ | 50 / 49 pieces | 172 / 174 | 800, 1,000, 2,000 |

For the 16A breaker, exactly 144 pieces were purchased 225 times in the 2025 comparison period and 247 times in 2026. Large purchases occurred in all nine observed months of both years.

Monthly pattern comparisons used only complete January-August months. Aggregate piece volumes for the retained IEK products show similar summer increases across the two years (Pearson correlation approximately 0.80); Systeme Electric is less similar (approximately 0.37). At individual product level, only 5.58% of 663 eligible series had correlation at least 0.70; the median correlation was approximately zero. Two years and eight paired months do not establish reliable seasonality for every SKU. Stock availability and exceptional transactions may confound these patterns.

## Actual inventory example: IMT35150

Source: Systeme Electric workbook "Товар в пути_SystemElectric на 22.09.2026.xlsx", sheet TDSheet, Excel row 484. This is the supplier-report snapshot, not a live inventory check.

| Reported sales | 2025 | 2026 |
| --- | ---: | ---: |
| July | 23,583 | 39,200 |
| August | 22,269 | 36,290 |
| Two-month total | 45,852 | 75,490 |

The report shows a 64.64% increase over these matching summer months. As of the report date:

- Stock: 43,539 pieces.
- Reserved: 4,008 pieces.
- Free stock: 39,531 pieces.
- Incoming quantity in the column labelled "СЭ в пути 24.09": 37,800 pieces.

At the simple July-August 2026 average of 37,745 pieces/month, free stock represents about 1.05 months. If the indicated delivery arrives fully and on time, free stock plus incoming quantity would be 77,331 pieces, about 2.05 months at that same rate. These are illustrative coverage calculations, not a demand forecast or a purchase recommendation.

## Data issues and unresolved questions

1. **Reports do not reconcile.** For IMT35150 in August 2025, the transit summary reports 22,269 pieces, the separate monthly-sales workbook reports 22,259, and positive detailed outgoing invoices total 31,776. Report scope, adjustment rules, and cut-off dates need confirmation. We have not established the cause and do not silently merge these totals.
2. **No anonymized customer ID** exists in either detailed sales report, although the brief anticipates it. Repeated batch sizes do not identify recurring customers.
3. **Monthly inventory snapshots are not stockout intervals.** They cannot establish exact days unavailable or missed sales. Missing cells must not automatically be treated as zero stock.
4. **Not all negatives are explained.** Returns/corrections require document-level interpretation before constructing net demand.
5. **Units and purchasing multiples matter.** Some IEK products are purchased in rolls but stocked in metres. Conversions must be explicit.
6. **September 2026 is incomplete.** Full-month historical comparisons and partial-month observations must remain distinct.
7. **Large does not mean exceptional.** Repeated wholesale-sized transactions should be distinguished from genuinely isolated events before any filtering.

## Planned next implementation steps

- Confirm source definitions and reconcile overlapping reports.
- Build validated imports with explicit SKU/unit mappings and data-quality warnings.
- Implement a transparent baseline forecast, growth/seasonality handling, and stockout adjustments where supported.
- Separate recurring large-batch demand from candidate one-off spikes; test missing scenarios with clearly labelled synthetic data if necessary.
- Calculate supplier-grouped recommendations using available stock, reservations, incoming deliveries, lead times, and order multiples.
- Add manager review, explanations, export, reproducible tests, and verified installation instructions.

These are planned features, not claims of completed implementation. No confidential customer identities, credentials, or raw data archives are included in this commit.
