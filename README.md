# Arcane Atlas

**Website:** [Arcane Atlas](https://saerdev.github.io/Arcane-Atlas/)

Arcane Atlas is a Warframe arcane price aggregator. It compares reported closed-order prices and trading volume for all arcanes on one page.

You can search and sort arcanes, compare 48-hour volume-weighted prices and latest traded-hour medians for rank 0 and max rank, check reported 48-hour trading volume, and hover over an arcane to see its effects and where to get it. Each arcane also links to its Warframe.market listing and Wiki page.

Both prices and volumes use the legacy v1 closed-order 48-hour statistics feed. Warframe.market updates its statistics hourly when volume is nonzero; configure cron-job.org to trigger the GitHub workflow once hourly. These are reported closed orders, not guaranteed completed player-to-player trades. Hours with no trades do not generate a new price.

This is an unofficial fan project, not affiliated with Digital Extremes or Warframe.market. Market data comes from Warframe.market; arcane information comes from WFCD. See [third-party notices](site/THIRD_PARTY_NOTICES.md) for credits.
