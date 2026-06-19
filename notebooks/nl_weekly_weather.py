"""
nl_weekly_weather.py
Fetches daily weather for 5 Netherlands cities from Open-Meteo (free, no API key)
and aggregates to ISO Mon-Sun weeks.

Output: nl_weekly_weather_<start>_<end>.csv

Usage:
    python nl_weekly_weather.py                        # Jan 2026 → today (actuals)
    python nl_weekly_weather.py --mode forecast        # next 16 days (forecast)
    python nl_weekly_weather.py --start 2026-03-01     # custom start date

Dependencies: requests, pandas
    pip install requests pandas

References:
    Historical API : https://open-meteo.com/en/docs/historical-weather-api
    Forecast API   : https://open-meteo.com/en/docs/
    WMO weather codes: https://open-meteo.com/en/docs#weathervariables
    ISO week date  : https://en.wikipedia.org/wiki/ISO_week_date
"""

import argparse
import sys
from datetime import date, timedelta

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CITIES = [
    {"name": "Amsterdam",  "lat": 52.37, "lon": 4.89},
    {"name": "Rotterdam",  "lat": 51.92, "lon": 4.48},
    {"name": "De Bilt",    "lat": 52.10, "lon": 5.18},
    {"name": "Eindhoven",  "lat": 51.44, "lon": 5.48},
    {"name": "Leeuwarden", "lat": 53.20, "lon": 5.80},
]

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "weather_code",
    "wind_gusts_10m_max",
]

HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL   = "https://api.open-meteo.com/v1/forecast"

# WMO weather code thresholds
# Full code table: https://open-meteo.com/en/docs#weathervariables
STORM_CODES      = range(95, 100)   # thunderstorm (with/without hail)
SNOW_CODES       = list(range(71, 78)) + [85, 86]  # snow fall / snow showers
HEAVY_RAIN_CODES = range(80, 95)    # showers / rain
STORM_GUST_KMH   = 75              # Beaufort 9+


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_city(city: dict, start: str, end: str, mode: str) -> pd.DataFrame:
    """Fetch daily data for one city; returns a tidy DataFrame."""
    if mode == "forecast":
        today = date.today().isoformat()
        params = {
            "latitude":  city["lat"],
            "longitude": city["lon"],
            "daily":     ",".join(DAILY_VARS),
            "timezone":  "Europe/Amsterdam",
            "past_days": (date.today() - date.fromisoformat(start)).days,
            "forecast_days": (date.fromisoformat(end) - date.today()).days + 1,
        }
        url = FORECAST_URL
    else:
        params = {
            "latitude":   city["lat"],
            "longitude":  city["lon"],
            "start_date": start,
            "end_date":   end,
            "daily":      ",".join(DAILY_VARS),
            "timezone":   "Europe/Amsterdam",
        }
        url = HISTORICAL_URL

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    daily = r.json()["daily"]

    df = pd.DataFrame(daily).rename(columns={"time": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df["city"] = city["name"]
    df["data_type"] = mode  # "historical" or "forecast"
    return df


# ---------------------------------------------------------------------------
# Event classification
# ---------------------------------------------------------------------------

def classify_events(row) -> str:
    wc    = int(row["weather_code"] or 0)
    prec  = float(row["precipitation_sum"] or 0)
    gust  = float(row["wind_gusts_10m_max"] or 0)

    events = set()
    if wc in STORM_CODES or gust >= STORM_GUST_KMH:
        events.add("storm")
    elif wc in SNOW_CODES:
        events.add("snow")
    elif wc in HEAVY_RAIN_CODES and prec >= 10:
        events.add("heavy_rain")
    elif prec >= 15:
        events.add("heavy_rain")

    return "|".join(sorted(events))


# ---------------------------------------------------------------------------
# Weekly aggregation
# ---------------------------------------------------------------------------

def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.copy()

    # ISO week start = Monday
    daily["week_start"] = daily["date"] - pd.to_timedelta(
        daily["date"].dt.weekday, unit="D"
    )
    daily["week_start"] = daily["week_start"].dt.date

    daily["event"] = daily.apply(classify_events, axis=1)

    def agg_events(series):
        ev = set()
        for val in series:
            if val:
                ev.update(val.split("|"))
        return "|".join(sorted(ev))

    weekly = (
        daily.groupby(["city", "data_type", "week_start"])
        .agg(
            week_end      =("date",                  lambda x: x.max().date()),
            days_in_week  =("date",                  "count"),
            temp_avg_c    =("temperature_2m_mean",   "mean"),
            temp_max_c    =("temperature_2m_max",    "max"),
            temp_min_c    =("temperature_2m_min",    "min"),
            precip_mm     =("precipitation_sum",     "sum"),
            max_gust_kmh  =("wind_gusts_10m_max",    "max"),
            adverse_events=("event",                 agg_events),
        )
        .reset_index()
    )

    weekly["temp_avg_c"]   = weekly["temp_avg_c"].round(1)
    weekly["temp_max_c"]   = weekly["temp_max_c"].round(1)
    weekly["temp_min_c"]   = weekly["temp_min_c"].round(1)
    weekly["precip_mm"]    = weekly["precip_mm"].round(1)
    weekly["max_gust_kmh"] = weekly["max_gust_kmh"].round(1)

    return weekly.sort_values(["week_start", "city"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Forecast vs actuals comparison
# ---------------------------------------------------------------------------

def compare_forecast_vs_actuals(
    forecast_csv: str, actuals_csv: str
) -> pd.DataFrame:
    """
    Join two CSVs (one forecast, one actuals) on city + week_start and compute
    deltas. Useful for the actuals vs forecast validation workflow.

    Usage:
        df = compare_forecast_vs_actuals("forecast_week1.csv", "actuals_week1.csv")
        df.to_csv("comparison.csv", index=False)
    """
    fc = pd.read_csv(forecast_csv)
    ac = pd.read_csv(actuals_csv)

    merged = fc.merge(
        ac, on=["city", "week_start"], suffixes=("_fc", "_act")
    )
    for col in ["temp_avg_c", "temp_max_c", "temp_min_c", "precip_mm"]:
        merged[f"{col}_delta"] = (
            merged[f"{col}_fc"] - merged[f"{col}_act"]
        ).round(1)

    return merged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="NL weekly weather fetcher")
    p.add_argument(
        "--mode", choices=["historical", "forecast"], default="historical",
        help="Data source: historical archive or live forecast (default: historical)"
    )
    p.add_argument(
        "--start", default="2026-01-01",
        help="Start date YYYY-MM-DD (default: 2026-01-01)"
    )
    p.add_argument(
        "--end", default=date.today().isoformat(),
        help="End date YYYY-MM-DD (default: today)"
    )
    p.add_argument(
        "--out", default=None,
        help="Output CSV path (default: auto-named)"
    )
    return p.parse_args()


def main():
    args = parse_args()
    start, end = args.start, args.end

    if date.fromisoformat(start) > date.fromisoformat(end):
        sys.exit("Error: --start must be before --end")

    print(f"Mode      : {args.mode}")
    print(f"Period    : {start} → {end}")
    print(f"Cities    : {', '.join(c['name'] for c in CITIES)}")
    print()

    frames = []
    for city in CITIES:
        print(f"  Fetching {city['name']}...")
        try:
            df = fetch_city(city, start, end, args.mode)
            frames.append(df)
        except requests.HTTPError as e:
            print(f"  ERROR: {e}")
            sys.exit(1)

    daily  = pd.concat(frames, ignore_index=True)
    weekly = to_weekly(daily)

    out = args.out or f"nl_weekly_weather_{start}_{end}.csv"
    weekly.to_csv(out, index=False)

    print(f"\nSaved {len(weekly)} rows → {out}")
    print(weekly.to_string(index=False))


if __name__ == "__main__":
    main()
