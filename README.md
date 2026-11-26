# Egypt Open Data API

Clean JSON for public Egyptian data, one simple API, with the source and license named in every response.

| Dataset | Endpoint | Source | License / terms |
|---|---|---|---|
| Official fuel prices | `/v1/fuel/latest`, `/v1/fuel/history` | Ministry of Petroleum and Mineral Resources | Official public prices, cited |
| EGP reference rates (USD, EUR, GBP, SAR, AED...) | `/v1/fx/latest`, `/v1/fx/{date}` | fawazahmed0/exchange-api | CC0 (market reference, not the CBE rate) |
| Inflation | `/v1/macro/inflation` | World Bank Open Data | CC BY 4.0 |
| GDP growth, GDP per capita, population, unemployment | `/v1/macro/{indicator}` | World Bank Open Data | CC BY 4.0 |
| Remittances received, official average exchange rate | `/v1/macro/remittances`, `/v1/macro/official_exchange_rate` | World Bank Open Data | CC BY 4.0 |

Not included on purpose: parallel-market ("black market") rates, and any source whose terms forbid commercial reuse.
The Central Bank of Egypt allows reuse with attribution but blocks automated access, so its rates are not scraped.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://localhost:8000/docs
```

## Examples

```bash
curl /v1/fuel/latest
curl "/v1/fx/latest?symbols=USD,EUR,SAR"
curl "/v1/macro/inflation?from_year=2015"
```

## Free tier and plans

The open instance allows 100 requests/day per IP. Paid plans with higher limits are offered through RapidAPI.

## Data freshness

Fuel and FX are cached for 6 hours, World Bank series for 24 hours. A GitHub Action records a fuel snapshot every day into `data/fuel_history.json`.

Code: MIT. Data: see each source's license above. Reference rates are indicative only, not financial advice.
