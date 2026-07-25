#!/usr/bin/env python3
"""
weather_alert.py

Checks tomorrow's forecast for River Falls, Wisconsin using the Open-Meteo
Forecast API (no API key required) and sends a Discord webhook message when
notable weather is expected.

Alert conditions (any one of these triggers an alert):
  - precipitation probability >= 60% AND rain total >= 0.01 in
  - snowfall >= 0.1 in
  - max temperature >= 95 F
  - min temperature <= 32 F

Duplicate alerts for the same forecast date are prevented by recording the
last alerted forecast date in alert_state.json.

Environment variables:
  DISCORD_WEBHOOK_URL   (required) Discord webhook URL. Never hardcode this.
  FORCE_ALERT           (optional) If set to "true", sends a test alert using
                         real forecast data regardless of whether conditions
                         are met, and does NOT update alert_state.json.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOCATION_NAME = "River Falls, Wisconsin"
# 44 deg 51' 31" N, 92 deg 37' 30" W, converted to decimal degrees.
LATITUDE = 44.8586
LONGITUDE = -92.6250
# IANA time zone name corresponding to Central Standard Time.
TIMEZONE = "America/Chicago"

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 15

STATE_FILE = Path(__file__).parent / "alert_state.json"

# Alert thresholds
RAIN_PROB_THRESHOLD_PCT = 60
RAIN_AMOUNT_THRESHOLD_IN = 0.01
SNOWFALL_THRESHOLD_IN = 0.1
HIGH_TEMP_THRESHOLD_F = 95
LOW_TEMP_THRESHOLD_F = 32


class WeatherAlertError(Exception):
    """Raised for any expected/handled failure in this script."""


# ---------------------------------------------------------------------------
# Forecast retrieval
# ---------------------------------------------------------------------------

def fetch_forecast():
    """
    Requests two days of daily forecast data from Open-Meteo and returns the
    parsed JSON response. Day index 0 is today, index 1 is tomorrow.
    """
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "daily": ",".join(
            [
                "precipitation_probability_max",
                "precipitation_sum",
                "snowfall_sum",
                "temperature_2m_max",
                "temperature_2m_min",
            ]
        ),
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "timezone": TIMEZONE,
        "forecast_days": 2,
    }

    try:
        response = requests.get(
            OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise WeatherAlertError(
            "Timed out while contacting the Open-Meteo API."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        raise WeatherAlertError(
            f"Open-Meteo API returned an HTTP error: {exc.response.status_code}."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise WeatherAlertError(
            f"Network error while contacting the Open-Meteo API: {exc}"
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise WeatherAlertError(
            "Open-Meteo API returned a response that was not valid JSON."
        ) from exc

    if "daily" not in data:
        raise WeatherAlertError(
            "Open-Meteo API response did not include the expected 'daily' data."
        )

    return data


def extract_tomorrow(daily_data):
    """
    Pulls the tomorrow (index 1) values out of the Open-Meteo 'daily' block
    and returns them as a plain dict. Raises WeatherAlertError if the
    expected fields/index are missing.
    """
    daily = daily_data["daily"]
    required_fields = [
        "time",
        "precipitation_probability_max",
        "precipitation_sum",
        "snowfall_sum",
        "temperature_2m_max",
        "temperature_2m_min",
    ]

    for field in required_fields:
        if field not in daily:
            raise WeatherAlertError(
                f"Open-Meteo response is missing expected field: {field}."
            )
        if len(daily[field]) < 2:
            raise WeatherAlertError(
                "Open-Meteo response did not include two full days of data."
            )

    return {
        "date": daily["time"][1],
        "rain_probability_pct": daily["precipitation_probability_max"][1],
        "rain_amount_in": daily["precipitation_sum"][1],
        "snowfall_in": daily["snowfall_sum"][1],
        "temp_max_f": daily["temperature_2m_max"][1],
        "temp_min_f": daily["temperature_2m_min"][1],
    }


# ---------------------------------------------------------------------------
# Alert evaluation
# ---------------------------------------------------------------------------

def evaluate_alert(forecast):
    """
    Given a tomorrow-forecast dict (see extract_tomorrow), returns a list of
    human-readable reason strings for any alert conditions that are met.
    An empty list means no alert is warranted.
    """
    reasons = []

    if (
        forecast["rain_probability_pct"] is not None
        and forecast["rain_amount_in"] is not None
        and forecast["rain_probability_pct"] >= RAIN_PROB_THRESHOLD_PCT
        and forecast["rain_amount_in"] >= RAIN_AMOUNT_THRESHOLD_IN
    ):
        reasons.append(
            f"Rain likely: {forecast['rain_probability_pct']:.0f}% chance, "
            f"{forecast['rain_amount_in']:.2f} in expected"
        )

    if (
        forecast["snowfall_in"] is not None
        and forecast["snowfall_in"] >= SNOWFALL_THRESHOLD_IN
    ):
        reasons.append(f"Snow expected: {forecast['snowfall_in']:.2f} in")

    if (
        forecast["temp_max_f"] is not None
        and forecast["temp_max_f"] >= HIGH_TEMP_THRESHOLD_F
    ):
        reasons.append(f"Extreme heat: high of {forecast['temp_max_f']:.0f} F")

    if (
        forecast["temp_min_f"] is not None
        and forecast["temp_min_f"] <= LOW_TEMP_THRESHOLD_F
    ):
        reasons.append(f"Freezing or below: low of {forecast['temp_min_f']:.0f} F")

    return reasons


# ---------------------------------------------------------------------------
# State handling (duplicate-alert prevention)
# ---------------------------------------------------------------------------

def load_state():
    """Loads alert_state.json, tolerating a missing or malformed file."""
    if not STATE_FILE.exists():
        return {"last_alert_date": None}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"last_alert_date": None}

    if "last_alert_date" not in state:
        state["last_alert_date"] = None

    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Discord messaging
# ---------------------------------------------------------------------------

def format_message(forecast, reasons, is_forced_test=False):
    date_obj = datetime.strptime(forecast["date"], "%Y-%m-%d")
    friendly_date = date_obj.strftime("%A, %B %d, %Y")

    header = "**Weather Alert**"
    if is_forced_test:
        header = "**Weather Alert (TEST / FORCE_ALERT)**"

    reason_lines = "\n".join(f"- {reason}" for reason in reasons) if reasons else "- (test message, no conditions met)"

    message = (
        f"{header}\n"
        f"Location: {LOCATION_NAME}\n"
        f"Forecast date: {friendly_date}\n"
        f"Reason(s):\n{reason_lines}\n"
        f"High / Low: {forecast['temp_max_f']:.0f} F / {forecast['temp_min_f']:.0f} F\n"
        f"Rain probability: {forecast['rain_probability_pct']:.0f}%\n"
        f"Rain amount: {forecast['rain_amount_in']:.2f} in\n"
        f"Snow amount: {forecast['snowfall_in']:.2f} in"
    )
    return message


def send_discord_webhook(webhook_url, message):
    try:
        response = requests.post(
            webhook_url,
            json={"content": message},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise WeatherAlertError(
            "Timed out while sending the Discord webhook message."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        raise WeatherAlertError(
            "Discord webhook returned an HTTP error "
            f"({exc.response.status_code}). Check that the webhook URL "
            "secret is valid and has not been revoked."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise WeatherAlertError(
            f"Network error while sending the Discord webhook message: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print(
            "ERROR: DISCORD_WEBHOOK_URL environment variable is not set. "
            "Set it in your .env file (local) or as a repository secret "
            "(GitHub Actions).",
            file=sys.stderr,
        )
        sys.exit(1)

    force_alert = os.environ.get("FORCE_ALERT", "false").strip().lower() == "true"

    try:
        raw_data = fetch_forecast()
        forecast = extract_tomorrow(raw_data)
    except WeatherAlertError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    reasons = evaluate_alert(forecast)
    state = load_state()

    if force_alert:
        print(
            f"FORCE_ALERT is enabled. Sending test alert for {forecast['date']} "
            "using real forecast data. alert_state.json will NOT be modified."
        )
        message = format_message(forecast, reasons, is_forced_test=True)
        try:
            send_discord_webhook(webhook_url, message)
        except WeatherAlertError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        print("Test alert sent successfully.")
        return

    if not reasons:
        print(f"No notable weather for {forecast['date']}. No alert sent.")
        return

    if state.get("last_alert_date") == forecast["date"]:
        print(
            f"Conditions met for {forecast['date']}, but an alert was already "
            "sent for this forecast date. Skipping to avoid duplicates."
        )
        return

    message = format_message(forecast, reasons, is_forced_test=False)
    try:
        send_discord_webhook(webhook_url, message)
    except WeatherAlertError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    state["last_alert_date"] = forecast["date"]
    save_state(state)
    print(f"Alert sent and alert_state.json updated for {forecast['date']}.")


if __name__ == "__main__":
    main()
