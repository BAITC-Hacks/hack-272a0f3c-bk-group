# Data Contract and Analytical Assumptions

This document defines what the replenishment model may infer from the supplied hackathon data, what it must not invent, and which checks are required before recommendations are shown to a manager.

## 1. Current input scope

The reviewed IEK and Systeme Electric archives contain:

- detailed sales movements;
- monthly sales summaries;
- monthly inventory snapshots;
- seasonality tables;
- goods in transit and current warehouse balances;
- minimum order quantities and order multiples.

The reports are historical snapshots, not live ERP connections. Every result must therefore display the source file and reporting date used.

## 2. Canonical keys and units

A product is identified by the tuple:

`supplier + supplier SKU + unit of measure`

Units must never be mixed. Pieces, metres, packages, rolls, and other units are calculated separately unless an explicit conversion factor is present and validated. Product names are descriptive fields, not reliable unique identifiers.

A sales transaction is identified by the full document field. Quantity is first aggregated per document and product before any large-order classification.

## 3. Sales movements

The forecasting baseline uses positive outgoing-invoice quantities for the selected period. Negative rows, corrections, returns, and other document types must be preserved during import and classified explicitly before net demand is calculated. Missing values must not silently become zero.

September 2026 is incomplete and must not be compared with a complete historical month. Year-over-year comparisons use matching date ranges.

## 4. Customer identity limitation

The supplied detailed sales files do not contain a stable anonymized customer identifier. Therefore the current solution cannot:

- determine whether two invoices belong to the same buyer;
- label a buyer as a repeat or permanent customer;
- build customer-level purchase history;
- infer customer loyalty from repeated quantities.

The system must not fabricate customer IDs from invoice numbers, dates, quantities, or document order. Repeated batch sizes indicate a product-level ordering pattern, not proof of the same customer.

If a future file provides `customer_id`, it may be added as an optional field. The application must still work when it is absent.

## 5. Large-order treatment

Large transactions are detected per product and unit, not with one global threshold. The current exploratory rule flags an invoice-product quantity only when it exceeds both:

- three times the product median; and
- `Q3 + 3 × IQR`.

A flag means “unusually large relative to this SKU history,” not “one-off customer order.” Repeated sizes such as 144, 288, or 432 pieces may represent normal carton or batch multiples. Large transactions must remain visible and must not automatically be deleted from demand history.

The model should separate at least three cases:

1. recurring large-batch demand;
2. isolated candidate spikes;
3. insufficient history to classify reliably.

## 6. Inventory and incoming supply

Available stock is calculated from explicitly supplied warehouse fields. Where present:

`free_stock = current_stock - reserved_quantity`

Incoming supply is added only when quantity, expected date, and status are sufficiently clear. A delayed or unconfirmed shipment must not be treated as guaranteed availability.

Monthly inventory snapshots are not exact stockout intervals. They do not reveal the precise day stock reached zero or the number of missed sales. Any stockout reconstruction from these files must be labelled as an estimate.

## 7. Source reconciliation

Overlapping reports do not always agree. For example, Systeme Electric SKU IMT35150 for August 2025 shows:

- 22,269 pieces in the transit summary;
- 22,259 pieces in the monthly-sales workbook;
- 31,776 pieces in positive detailed outgoing invoices.

These values must not be silently averaged or merged. The import layer should expose the discrepancy and require a documented source-priority rule.

## 8. Recommendation inputs

A purchase recommendation may use only validated fields:

- historical demand by SKU and unit;
- current stock and reservations;
- confirmed incoming quantity and expected arrival;
- seasonality or growth inputs with a stated source;
- supplier lead time when available;
- minimum order quantity and order multiple.

The output must show the suggested quantity, supplier grouping, calculation date, assumptions, and a plain-language explanation. A manager must be able to edit or reject the result before export. The application must never send an order automatically.

## 9. Required validation checks

Before calculation, the pipeline should report:

- duplicate rows or duplicate document-product records;
- missing supplier, SKU, date, quantity, or unit;
- unknown document types;
- negative or zero quantities requiring interpretation;
- mixed units for the same SKU;
- impossible stock or reservation values;
- inconsistent totals across source files;
- partial-month data;
- insufficient history for a reliable threshold or forecast.

## 10. Test scenarios

At minimum, tests should cover:

- a regular SKU with stable monthly sales;
- a recurring wholesale batch pattern;
- a single extreme transaction;
- missing customer identifiers;
- an incomplete month;
- a stockout-like inventory snapshot;
- incoming stock arriving before and after the forecast horizon;
- order quantities rounded to supplier multiples;
- conflicting totals from two reports.

Synthetic data may be used to test missing scenarios, but it must be clearly labelled and never presented as historical business data.

## 11. Scope of this commit

This document records the agreed data boundaries and modelling assumptions. It does not claim that the forecast engine, user interface, live ERP integration, or automatic ordering workflow has already been implemented.
