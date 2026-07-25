#!/usr/bin/env python3
"""
weather_alert.py

Sends the current actual temperature for River Falls, Wisconsin to Discord
every time this script runs (intended to run every 20 minutes via GitHub
Actions), using the Open-Meteo Forecast API (no API key required).

Alongside the regular update, a highlighted alert section is appended when
any of the following conditions are met for tomorrow's forecast:
- precipitation probability >= 60% AND rain total >= 0.01 in
- snowfall >= 0.1 in
- max temperature >= 95 F
- min temperature <= 32 F

These no longer control whether a message is sent -- a message is sent on
every run regardless. They only control whether an "alert" section is added.

Environment variables:
    DISCORD_WEBHOOK_URL (required) Discord webhook URL. Never hardcode this.
"""

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

# Alert thresholds (for the highlighted section only -- they no longer
# decide whether a message is sent)
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
    Requests current conditions plus two days of daily forecast data from
    Open-Meteo and returns the parsed JSON response. Daily index 0 is
    today, index 1 is tomorrow.
    """
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": ",".join(
            [
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "wind_speed_10m",
            ]
        ),
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
        "wind_speed_unit": "mph",
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

    if "current" not in data:
        raise WeatherAlertError(
            "Open-Meteo API response did not include the expected 'current' data."
        )
    if "daily" not in data:
        raise WeatherAlertError(
            "Open-Meteo API response did not include the expected 'daily' data."
        )

    return data


def extract_current(data):
    """Pulls today's actual current conditions out of the API response."""
    current = data["current"]
    required_fields = [
        "time",
        "temperature_2m",
        "apparent_temperature",
        "relative_humidity_2m",
        "wind_speed_10m",
    ]
    for field in required_fields:
        if field not in current:
            raise WeatherAlertError(
                f"Open-Meteo response is missing expected current field: {field}."
            )

    return {
        "time": current["time"],
        "temp_f": current["temperature_2m"],
        "feels_like_f": current["apparent_temperature"],
        "humidity_pct": current["relative_humidity_2m"],
        "wind_mph": current["wind_speed_10m"],
    }


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
# Alert evaluation (now only used to decorate the message, not gate it)
# ---------------------------------------------------------------------------

def evaluate_alert(forecast):
    """
    Given a tomorrow-forecast dict, returns a list of human-readable reason
    strings for any notable conditions. An empty list just means the
    regular temperature update goes out with no extra alert section.
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
# Discord messaging
# ---------------------------------------------------------------------------

def format_message(current, forecast, reasons):
    date_obj = datetime.strptime(forecast["date"], "%Y-%m-%d")
    friendly_date = date_obj.strftime("%A, %B %d, %Y")

    header = "**Weather Update**" if not reasons else "**Weather Update (Alert)**"

    lines = [
        header,
        f"Location: {LOCATION_NAME}",
        f"Current temperature: {current['temp_f']:.0f} F "
        f"(feels like {current['feels_like_f']:.0f} F)",
        f"Humidity: {current['humidity_pct']:.0f}% | Wind: {current['wind_mph']:.0f} mph",
        f"Tomorrow's high/low ({friendly_date}): "
        f"{forecast['temp_max_f']:.0f} F / {forecast['temp_min_f']:.0f} F",
    ]

    if reasons:
        reason_lines = "\n".join(f"- {reason}" for reason in reasons)
        lines.append(f"Tomorrow's alert(s):\n{reason_lines}")

    return "\n".join(lines)


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

    try:
        raw_data = fetch_forecast()
        current = extract_current(raw_data)
        forecast = extract_tomorrow(raw_data)
    except WeatherAlertError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    reasons = evaluate_alert(forecast)
    message = format_message(current, forecast, reasons)

    try:
        send_discord_webhook(webhook_url, message)
    except WeatherAlertError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Update sent for {current['time']}.")


if __name__ == "__main__":
    main()
