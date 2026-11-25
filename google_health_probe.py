"""Standalone Google Health API latency probe. Tokens live in memory only."""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
URL = "https://health.googleapis.com/v4/users/me/dataTypes/heart-rate/dataPoints"
LOG = Path("google-health-probe.log")

def utcnow():
    return datetime.now(timezone.utc)

def stamp(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")

def query_params(now, window_minutes):
    field = "heart_rate.sample_time.physical_time"
    return {
        "filter": f'{field} >= "{stamp(now - timedelta(minutes=window_minutes))}" AND {field} < "{stamp(now)}"',
        "pageSize": 10000,
        "dataSourceFamily": "users/me/dataSourceFamilies/google-wearables",
    }

def sample(point):
    try:
        hr = point["heartRate"]
        timestamp = datetime.fromisoformat(hr["sampleTime"]["physicalTime"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None
        bpm = int(hr["beatsPerMinute"])
        if bpm <= 0:
            return None
        return timestamp, bpm
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

def log(message):
    line = f"[{datetime.now():%H:%M:%S}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as out:
        out.write(line + "\n")

def fetch_samples(session, now, window):
    params = query_params(now, window)
    result = []
    tokens = set()
    for _ in range(20):
        with session.get(URL, params=params, timeout=20, allow_redirects=False) as response:
            if response.status_code != 200:
                return response.status_code, []
            payload = response.json()
        result.extend(s for p in payload.get("dataPoints", []) if (s := sample(p)) is not None)
        token = payload.get("nextPageToken")
        if not token:
            return 200, result
        if token in tokens:
            raise ValueError("Repeated page token")
        tokens.add(token)
        params["pageToken"] = token
    raise ValueError("Page limit exceeded")

def run(args):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import AuthorizedSession

    config = json.loads(Path(args.credentials).read_text(encoding="utf-8"))
    installed = config.get("installed", {})
    if (not installed.get("client_id", "").endswith(".apps.googleusercontent.com")
            or installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
            and installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/v2/auth"
            or installed.get("token_uri") != "https://oauth2.googleapis.com/token"):
        raise ValueError("Use an original Google OAuth Desktop app credentials JSON")
    print("Opening Google sign-in in your browser. No password is collected by this script.")
    flow = InstalledAppFlow.from_client_config(
        config, scopes=[SCOPE], autogenerate_code_verifier=True)
    credentials = flow.run_local_server(host="127.0.0.1", port=0,
                                        open_browser=True, timeout_seconds=180,
                                        authorization_prompt_message="",
                                        success_message="Authorization complete. Return to the terminal.")
    LOG.write_text("", encoding="utf-8")
    log(f"Google Health freshness probe: interval={args.interval}s; window={args.window}min")
    log("Diagnostic log contains timing and counts only. BPM is not saved.")
    latest = None
    deadline = time.monotonic() + args.duration * 60
    session = AuthorizedSession(credentials)
    try:
        while time.monotonic() < deadline:
            started = time.monotonic()
            try:
                status, samples = fetch_samples(session, utcnow(), args.window)
                now = utcnow()
                elapsed = time.monotonic() - started
                if status == 200:
                    if samples:
                        newest, bpm = max(samples, key=lambda s: s[0])
                        age = (now - newest).total_seconds()
                        advanced = latest is None or newest > latest
                        if advanced:
                            latest = newest
                        log(f"samples={len(samples)} latest={stamp(newest)} age={age:.1f}s "
                            f"advanced={advanced} request={elapsed:.2f}s")
                        print(f"  Latest heart rate: {bpm} BPM (not logged)", flush=True)
                    else:
                        log(f"No samples in the last {args.window} min; request={elapsed:.2f}s")
                elif status in (401, 403, 400, 404):
                    log(f"HTTP {status}: check Google account linking, API enablement, consent and query.")
                    break
                elif status == 429 or status >= 500:
                    log(f"HTTP {status}: pausing for 60 seconds")
                    time.sleep(min(60, max(0, deadline - time.monotonic())))
                    continue
                else:
                    log(f"Unexpected HTTP {status}; stopping")
                    break
            except Exception as exc:
                # Avoid printing URLs, tokens, response payloads or credential contents.
                log(f"Request failed: {type(exc).__name__}; stopping")
                break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(remaining, max(0, args.interval - (time.monotonic() - started))))
    finally:
        session.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", default="google-health-client.json")
    parser.add_argument("--interval", type=int, default=15, choices=range(5, 121), metavar="5-120")
    parser.add_argument("--duration", type=int, default=20, choices=range(1, 121), metavar="1-120")
    parser.add_argument("--window", type=int, default=30, choices=range(5, 121), metavar="5-120")
    args = parser.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        print("Stopped by user.")
    except ImportError:
        print("Install dependencies: py -3.12 -m pip install google-auth-oauthlib requests")
    except FileNotFoundError:
        print("OAuth Desktop credentials JSON not found. See GOOGLE_HEALTH_PROBE.md.")
    except Exception as exc:
        print(f"Setup failed: {type(exc).__name__}. Check OAuth desktop credentials and consent.")
    print("No token file is saved. Diagnostic log, if created:", LOG.resolve())

if __name__ == "__main__":
    main()
