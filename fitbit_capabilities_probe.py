"""Read-only Google Health paired device capability diagnostic."""
import argparse
import json
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/googlehealth.settings.readonly"
URL = "https://health.googleapis.com/v4/users/me/pairedDevices"
INTEREST = ("HEART_RATE", "LIVE_DATA", "ASPEN_SUPPORTED",
            "ASPEN_REMOTE_UI_SUPPORTED", "DOMAIN_KEY_SYNC", "BONDING",
            "TRACKER_CHANNEL_CHARACTERISTIC", "DIRECT_DEVICE_SETTINGS_SUPPORTED")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", default="google-health-client.json")
    args = parser.parse_args()
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import AuthorizedSession
        config = json.loads(Path(args.credentials).read_text(encoding="utf-8-sig"))
        client = config["installed"]
        if (not client["client_id"].endswith(".apps.googleusercontent.com")
                or client["auth_uri"] not in ("https://accounts.google.com/o/oauth2/auth",
                                             "https://accounts.google.com/o/oauth2/v2/auth")
                or client["token_uri"] != "https://oauth2.googleapis.com/token"):
            raise ValueError("Unexpected OAuth configuration")
        print("Opening Google sign-in. Read-only device settings permission.")
        flow = InstalledAppFlow.from_client_config(config, scopes=[SCOPE],
                                                   autogenerate_code_verifier=True)
        creds = flow.run_local_server(host="127.0.0.1", port=0,
                                      open_browser=True, timeout_seconds=180,
                                      authorization_prompt_message="",
                                      success_message="Authorization complete. Return to the terminal.")
        with AuthorizedSession(creds) as session:
            token = None
            for _ in range(10):
                params = {"pageToken": token} if token else {}
                with session.get(URL, params=params, timeout=20, allow_redirects=False) as response:
                    print(f"HTTP {response.status_code}")
                    if response.status_code != 200:
                        print("Check API enablement, test user and settings.readonly consent.")
                        return
                    payload = response.json()
                devices = payload.get("pairedDevices", [])
                for device in devices:
                    print("Device:", device.get("deviceVersion", "(unknown)"))
                    features = set(device.get("features") or [])
                    if not features:
                        print("No feature list supplied; capabilities unknown.")
                    else:
                        for key in INTEREST:
                            print(f"  {key}: {'listed' if key in features else 'not listed'}")
                    print("  Last sync:", device.get("lastSyncTime", "(unknown)"))
                if not devices:
                    print("No paired devices on this page.")
                token = payload.get("nextPageToken")
                if not token:
                    break
    except KeyboardInterrupt:
        print("Stopped.")
    except Exception as exc:
        print(f"Diagnostic failed: {type(exc).__name__}")
    finally:
        print("No tokens, keys or device identifiers saved.")

if __name__ == "__main__":
    main()
