# Google Health freshness experiment

This is a standalone diagnostic, not part of the Windows executable. It only
queries recent heart-rate records from Google/Fitbit wearables. It does not
force device sync or send heart rate to MyWhoosh.

1. In https://console.cloud.google.com/ create a separate personal test project.
2. Enable Google Health API (health.googleapis.com).
3. Configure Google Auth Platform branding, audience External / Testing, and
   add your own Google Health account as a test user.
4. Under Data Access add
   https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
   (this grants read access to a broader set of health metrics, although the probe
   queries only heart rate).
5. Under Clients create an OAuth client of type Desktop app. Download its JSON
   to google-health-client.json in this directory. Do not commit or share it.
6. Install optional dependencies:
   `py -3.12 -m pip install google-auth-oauthlib requests`
7. Run `py -3.12 google_health_probe.py --interval 15 --duration 20`.
   Authorize your personal Google account in the system browser. The script
   uses a temporary loopback redirect and PKCE, with tokens kept in memory only.
8. Wear the watch with Bluetooth active; compare several minutes of Google Health
   in background, then foreground, then manually sync. Note the times you
   change conditions. The age field is seconds since the latest sample was
   recorded, not precise server ingestion latency. The advanced flag indicates
   a newer latest sample. BPM appears on screen only; the log contains sample
   counts, timestamps and age, not tokens or BPM. Each run replaces the log.

References:
- https://developers.google.com/health/setup
- https://developers.google.com/health/filters
- https://developers.google.com/health/data-types/vitals

No production OAuth client, cloud project or credentials are supplied. Testing
mode may expire refresh tokens after seven days, but this probe does not persist
tokens anyway. Google documents sync-dependent availability; a 1-second sample
resolution is not a 1-second delivery guarantee.
