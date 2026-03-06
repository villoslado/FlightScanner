# FlightScanner

Polls Amadeus for SEA ↔ FCO fares on a schedule, stores observations in SQLite, and logs deals when a price drops below the rolling average.

## Setup

pip install amadeus apscheduler python-dotenv

Create a `.env` file:

AMADEUS_API_KEY=your_key
AMADEUS_API_SECRET=your_secret

Get free sandbox credentials at developers.amadeus.com

## Run

python flight_scanner.py

Scans once immediately, then every 6 hours. Logs deals to console and `deals.log`.

## Config

Edit the top of `flight_scanner.py`:

| Variable             | Default      | Description                    |
|----------------------|--------------|--------------------------------|
| SCAN_FROM            | 2026-06-01   | Start date                     |
| SCAN_TO              | 2026-08-31   | End date                       |
| DEAL_THRESHOLD_PCT   | 0.10         | % below avg to flag as deal    |
| POLL_INTERVAL_HOURS  | 6            | Polling cadence                |

## Notes

- Sandbox returns simulated fares. For real prices, apply for production access on the Amadeus dashboard and pass `hostname='production'` to `Client()`.
- First run builds the baseline — deals start appearing on subsequent runs.
- `flights.db` stores all fare history. `deals.log` stores flagged deals.
