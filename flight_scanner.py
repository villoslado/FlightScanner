import os
import sqlite3
import logging
import time
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from amadeus import Client, ResponseError
from apscheduler.schedulers.blocking import BlockingScheduler

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

ORIGIN      = "SEA"
DESTINATION = "FCO"
CURRENCY    = "USD"
ADULTS      = 1
DB_PATH     = "flights.db"
LOG_PATH    = "deals.log"

SCAN_FROM = "2026-10-01"  # start date (inclusive)
SCAN_TO   = "2026-12-31"  # end date (inclusive)

DEAL_THRESHOLD_PCT  = 0.10  # 10% below average = a deal
POLL_INTERVAL_HOURS = 6

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Database ──────────────────────────────────────────────────────────────────

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fares (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            origin       TEXT    NOT NULL,
            destination  TEXT    NOT NULL,
            depart_date  TEXT    NOT NULL,
            price        REAL    NOT NULL,
            currency     TEXT    NOT NULL,
            carrier      TEXT,
            fetched_at   TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            origin       TEXT    NOT NULL,
            destination  TEXT    NOT NULL,
            depart_date  TEXT    NOT NULL,
            price        REAL    NOT NULL,
            avg_price    REAL    NOT NULL,
            pct_below    REAL    NOT NULL,
            carrier      TEXT,
            alerted_at   TEXT    NOT NULL
        )
    """)
    conn.commit()


def save_fare(conn, origin, destination, depart_date, price, currency, carrier):
    conn.execute(
        """INSERT INTO fares (origin, destination, depart_date, price, currency, carrier, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (origin, destination, depart_date, price, currency, carrier, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_average_price(conn, origin, destination, depart_date):
    row = conn.execute(
        """SELECT AVG(price) FROM fares
           WHERE origin=? AND destination=? AND depart_date=?""",
        (origin, destination, depart_date),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def already_alerted_today(conn, origin, destination, depart_date, price):
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        """SELECT 1 FROM deals
           WHERE origin=? AND destination=? AND depart_date=?
             AND price=? AND alerted_at LIKE ?""",
        (origin, destination, depart_date, price, f"{today}%"),
    ).fetchone()
    return row is not None


def save_deal(conn, origin, destination, depart_date, price, avg_price, pct_below, carrier):
    conn.execute(
        """INSERT INTO deals (origin, destination, depart_date, price, avg_price, pct_below, carrier, alerted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (origin, destination, depart_date, price, avg_price, pct_below, carrier, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

# ── Amadeus ───────────────────────────────────────────────────────────────────

def build_client():
    key    = os.environ.get("AMADEUS_API_KEY")
    secret = os.environ.get("AMADEUS_API_SECRET")
    if not key or not secret:
        raise EnvironmentError(
            "Set AMADEUS_API_KEY and AMADEUS_API_SECRET in your .env file.\n"
            "Get free sandbox credentials at https://developers.amadeus.com"
        )
    return Client(client_id=key, client_secret=secret)


def fetch_fares(client, origin, destination, depart_date):
    try:
        response = client.shopping.flight_offers_search.get(
            originLocationCode=origin,
            destinationLocationCode=destination,
            departureDate=depart_date,
            adults=ADULTS,
            currencyCode=CURRENCY,
            max=5,
            nonStop="false",
        )
        results = []
        for offer in response.data:
            price   = float(offer["price"]["total"])
            carrier = offer["itineraries"][0]["segments"][0]["carrierCode"]
            results.append({"price": price, "carrier": carrier})
        return results
    except ResponseError as e:
        log.warning(f"Amadeus error for {origin}→{destination} on {depart_date}: {e}")
        return []

# ── Core scan logic ───────────────────────────────────────────────────────────

def scan(directions):
    client = build_client()
    conn   = sqlite3.connect(DB_PATH)
    init_db(conn)

    from_date  = datetime.strptime(SCAN_FROM, "%Y-%m-%d").date()
    to_date    = datetime.strptime(SCAN_TO,   "%Y-%m-%d").date()
    date_range = [from_date + timedelta(days=d) for d in range((to_date - from_date).days + 1)]

    total_fetched = 0
    total_deals   = 0

    for origin, destination in directions:
        for dep_date in date_range:
            date_str = dep_date.isoformat()
            offers   = fetch_fares(client, origin, destination, date_str)
            time.sleep(0.5)  # pause 250–500ms between calls

            for offer in offers:
                price   = offer["price"]
                carrier = offer["carrier"]
                save_fare(conn, origin, destination, date_str, price, CURRENCY, carrier)
                total_fetched += 1

                avg = get_average_price(conn, origin, destination, date_str)
                if avg and price < avg * (1 - DEAL_THRESHOLD_PCT):
                    pct_below = (avg - price) / avg
                    if not already_alerted_today(conn, origin, destination, date_str, price):
                        save_deal(conn, origin, destination, date_str, price, avg, pct_below, carrier)
                        log.info(
                            f"🔥 DEAL  {origin}→{destination}  {date_str}  "
                            f"${price:.0f}  (avg ${avg:.0f}, {pct_below:.1%} below)  carrier={carrier}"
                        )
                        total_deals += 1

    log.info(f"Scan complete. Fetched {total_fetched} fares, flagged {total_deals} deals.")
    conn.close()


def run_scan():
    log.info("── Starting scan ──────────────────────────────────")
    directions = [
        (ORIGIN, DESTINATION),
        (DESTINATION, ORIGIN),
    ]
    scan(directions)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_scan()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_scan, "interval", hours=POLL_INTERVAL_HOURS)
    log.info(f"Scheduler running — polling every {POLL_INTERVAL_HOURS}h. Ctrl+C to stop.")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scanner stopped.")
