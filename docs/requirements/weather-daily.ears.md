# Requirements: daily aggregation of world weather observations (EARS)

- Status: approved (2026-10-25)
- Source: the original requirements document (Japanese), rewritten in EARS for the requirements reviewer
- Only requirements that can be checked from the template, the usage file (usage.json) and the price are listed here

## Requirements

1. R-01: THE SYSTEM SHALL start the aggregation once a day, after the source file is updated (09:00 to 11:59 UTC).
2. R-02: WHEN a daily run starts THE SYSTEM SHALL finish replacing the output within 60 minutes.
3. R-03: THE SYSTEM SHALL provide temporary storage larger than the year-end source file (1,384 MB, the size of 2024.csv).
4. R-04: WHILE it processes a year-end source file THE SYSTEM SHALL keep at least 10% of the configured memory free at the measured peak.
5. R-05: THE SYSTEM SHALL keep the on-demand monthly cost of the aggregation under 1.00 USD.

## Notes

- R-04 is checked against the peak memory measured with 2025.csv (1,311 MB), the latest complete year, on 2026-10-25.
- The source file grows every day until the end of the year, so the year-end size is the worst case.
