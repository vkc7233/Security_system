"""API and rule tests. A fake face_recognition module stands in for dlib."""
import io
import sys
import types
from datetime import datetime

import cv2
import numpy as np
import pytest

# ---- fake face_recognition: one face per image, encoding = mean pixel value ----
fake = types.ModuleType("face_recognition")
fake.face_locations = lambda img: [] if img.mean() < 5 else [(10, 110, 110, 10)]
fake.face_encodings = lambda img, locs=None: [np.full(128, img.mean() / 255.0)]
fake.face_distance = lambda known, enc: np.linalg.norm(np.asarray(known) - enc, axis=1)
sys.modules["face_recognition"] = fake

from app import create_app  # noqa: E402
from security.config import Config  # noqa: E402
from security.engine import classify  # noqa: E402


def jpeg(value):
    img = np.full((200, 200, 3), value, dtype=np.uint8)
    return io.BytesIO(cv2.imencode(".jpg", img)[1].tobytes())


@pytest.fixture()
def client(tmp_path):
    class Cfg(Config):
        DB_PATH = str(tmp_path / "t.db")
        FACES_DIR = str(tmp_path / "faces")
        SNAPSHOT_DIR = str(tmp_path / "snaps")
        TELEGRAM_BOT_TOKEN = ""
        ADMIN_PASSWORD = ""
    app = create_app(Cfg)
    app.testing = True
    c = app.test_client()
    c.sg = app.extensions["siteguard"]
    return c


def enrol(client, name, value, category="Staff", **extra):
    data = {"name": name, "category": category, "image": (jpeg(value), "a.jpg"), **extra}
    return client.post("/api/persons", data=data, content_type="multipart/form-data")


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and b"SiteGuard" in r.data


def test_enrol_list_and_duplicate_block(client):
    assert enrol(client, "Ramesh", 100).status_code == 200
    dup = enrol(client, "Ramesh Copy", 100)
    assert dup.status_code == 422 and "Duplicate" in dup.get_json()["status"]
    people = client.get("/api/persons").get_json()
    assert [p["name"] for p in people] == ["Ramesh"] and people[0]["samples"] == 1


def test_enrol_validation(client):
    assert client.post("/api/persons", data={"category": "Staff"}).status_code == 400
    assert enrol(client, "X", 100, category="Hacker").status_code == 400
    no_face = enrol(client, "Dark", 0)
    assert no_face.status_code == 422 and "No face" in no_face.get_json()["status"]


def test_filename_not_user_controlled(client, tmp_path):
    assert enrol(client, "../../evil", 120).status_code == 200
    files = list((tmp_path / "faces").iterdir())
    assert len(files) == 1 and files[0].name.startswith("person_")


def test_legacy_route_still_works(client):
    r = client.post("/capture_image", data={"name": "Legacy", "image": (jpeg(90), "c.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 200


def test_update_suspend_and_delete(client):
    pid = enrol(client, "Suresh", 150).get_json()["id"]
    assert client.patch(f"/api/persons/{pid}", json={"category": "Blacklisted"}).status_code == 200
    assert client.patch(f"/api/persons/{pid}", json={"active": 0}).status_code == 200
    assert client.sg["engine"].status()["gallery_size"] == 0
    assert client.delete(f"/api/persons/{pid}").status_code == 200
    assert client.get("/api/persons").get_json() == []


def test_events_register_ack_and_csv(client):
    db = client.sg["db"]
    pid = enrol(client, "Anil", 60, category="Labour").get_json()["id"]
    day = datetime.now().date().isoformat()
    db.add_event(ts=f"{day}T08:05:00", person_id=pid, name="Anil", category="Labour", severity="Low")
    db.add_event(ts=f"{day}T17:35:00", person_id=pid, name="Anil", category="Labour", severity="Low")
    eid = db.add_event(ts=f"{day}T22:10:00", name="Unknown", category="Unknown", severity="High",
                       reason="Unknown person outside working hours")

    status = client.get("/api/status").get_json()
    assert status["stats"]["open_alerts"] == 1 and status["stats"]["entries_today"] == 1

    reg = client.get(f"/api/register?date={day}").get_json()
    assert reg["rows"][0]["hours_on_site"] == 9.5
    assert reg["hourly"][22]["unknown"] == 1 and reg["hourly"][8]["known"] == 1

    csv_text = client.get(f"/api/register.csv?date={day}").data.decode()
    assert "Anil,Labour" in csv_text and "08:05" in csv_text

    assert client.post(f"/api/events/{eid}/ack", json={"note": "Guard checked"}).status_code == 200
    assert client.get("/api/status").get_json()["stats"]["open_alerts"] == 0
    assert len(client.get(f"/api/events?date={day}&severity=High").get_json()) == 1


def test_basic_auth(tmp_path):
    class Cfg(Config):
        DB_PATH = str(tmp_path / "a.db")
        FACES_DIR = str(tmp_path / "f")
        SNAPSHOT_DIR = str(tmp_path / "s")
        ADMIN_PASSWORD = "s3cret"
    c = create_app(Cfg).test_client()
    assert c.get("/api/status").status_code == 401
    ok = c.get("/api/status", headers={"Authorization": "Basic YWRtaW46czNjcmV0"})  # admin:s3cret
    assert ok.status_code == 200


def test_legacy_db_migration(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE known_faces (id INTEGER PRIMARY KEY, name TEXT, encoding BLOB)")
    for name in ("A", "A", "B"):
        con.execute("INSERT INTO known_faces (name, encoding) VALUES (?, ?)", (name, np.zeros(128).tobytes()))
    con.commit()
    con.close()
    from security.database import Database
    db = Database(str(path))
    people = db.list_persons()
    assert [(p["name"], p["samples"]) for p in people] == [("A", 2), ("B", 1)]
    Database(str(path))  # second start must not duplicate
    assert len(db.list_persons()) == 2


@pytest.mark.parametrize("meta,hour,expected", [
    (None, 10, "Medium"),
    (None, 23, "High"),
    ({"category": "Blacklisted"}, 10, "Critical"),
    ({"category": "Visitor", "valid_until": "2020-01-01"}, 10, "High"),
    ({"category": "Labour"}, 2, "Medium"),
    ({"category": "Staff"}, 2, "Low"),
    ({"category": "Engineer"}, 11, "Low"),
])
def test_classify_rules(meta, hour, expected):
    assert classify(meta, datetime(2026, 9, 30, hour, 0), Config)[1] == expected


def test_engine_loop_logs_with_cooldown(client, monkeypatch):
    """Drive the real engine loop with a fake camera: one known + one unknown face."""
    import time
    import security.engine as eng
    enrol(client, "Known Guy", 100)
    frames = [np.full((240, 320, 3), v, dtype=np.uint8) for v in (100, 200) * 20]

    class FakeCap:
        def __init__(self, *_):
            self.i = 0
        def isOpened(self):
            return True
        def read(self):
            time.sleep(0.005)
            self.i += 1
            return True, frames[self.i % len(frames)]
        def release(self):
            pass

    monkeypatch.setattr(eng.cv2, "VideoCapture", FakeCap)
    sent = []
    engine = client.sg["engine"]
    monkeypatch.setattr(engine.notifier, "send", lambda text, photo=None: sent.append((text, photo)))
    engine.cfg.PROCESS_EVERY_N_FRAMES = 1
    engine.cfg.FRAME_SCALE = 1.0
    assert engine.start()
    time.sleep(0.6)
    assert engine.status()["camera_ok"] and engine._jpeg
    engine.stop()
    engine._thread.join(2)

    events = client.sg["db"].list_events()
    assert sorted(e["name"] for e in events) == ["Known Guy", "Unknown"]  # cooldown dedupes repeats
    assert all(e["snapshot"] for e in events)
    assert len(sent) == 1 and "Unknown" in sent[0][0]
