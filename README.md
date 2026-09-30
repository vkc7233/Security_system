# SiteGuard — Face Recognition Site Security

A site security system for construction projects. It identifies people at the gate, keeps an automatic attendance register, escalates alerts by risk, and produces an emergency muster (roll-call) list.

## Features

| Module | What it does |
| --- | --- |
| Live Monitor | Arm/disarm, live annotated camera feed in the browser, KPI tiles, open alerts, "on site now" list, audible alert tone |
| Risk rule engine | Blacklisted → **Critical**; expired access pass → High; unknown after hours → High; unknown in hours → Medium; labour/vendor/visitor after hours → Medium |
| Enrolment | 1–5 photos per person (webcam, upload or armed security camera); rejects no-face, multi-face, too-small and duplicate faces |
| People Directory | Categories (Staff, Engineer, Contractor, Labour, Vendor, Visitor, Blacklisted), project tagging, pass expiry, suspend/reactivate, add more photos |
| Event Log | Every detection with snapshot evidence, severity filter, acknowledge with action note |
| Site Register | Automatic daily attendance (first in, last seen, hours on site), hourly activity chart, CSV export, print |
| Emergency Muster | Everyone seen in the last N minutes; tick off at the assembly point; shows missing count |
| Alerts | Telegram message + snapshot for Medium and above, sent in the background (camera loop never blocks) |
| Resilience | Camera auto-reconnect with backoff, "camera offline / tampered" alert, supports USB webcam or RTSP CCTV |

## Setup

```bash
pip install -r requirements.txt      # face_recognition needs dlib (CMake + C++ build tools)
cp .env.example .env                 # then fill in the values
python app.py                        # open http://127.0.0.1:5000
```

All settings (camera source, Telegram, working hours, login password, tolerances) are in `.env`. See `.env.example`.

Existing data in `faces.db` (the old `known_faces` table) is migrated automatically on first start.

## Tests

```bash
pip install pytest
python -m pytest -q tests
```

Tests use a stub in place of `face_recognition`, so dlib is not needed to run them.

## Security notes

- Set `ADMIN_PASSWORD` before exposing the dashboard on a network (`HOST=0.0.0.0`).
- Never commit `.env`, `faces.db`, `known_faces/` or `snapshots/` — they hold credentials and biometric data.
