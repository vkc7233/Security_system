"""SiteGuard - face recognition site security for construction projects."""
import csv
import hmac
import io
import logging
import os
import uuid
from datetime import date

import cv2
import numpy as np
from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory

from security.config import CATEGORIES, PROJECTS, SEVERITIES, Config
from security.database import Database
from security.engine import EnrolmentError, SecurityEngine, encode_single_face, face_recognition
from security.notifier import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(cfg=Config):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg.MAX_UPLOAD_MB * 1024 * 1024
    os.makedirs(cfg.FACES_DIR, exist_ok=True)
    os.makedirs(cfg.SNAPSHOT_DIR, exist_ok=True)

    db = Database(cfg.DB_PATH)
    notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    engine = SecurityEngine(db, notifier, cfg)
    app.extensions["siteguard"] = {"db": db, "engine": engine, "notifier": notifier}

    # ---- access control ---------------------------------------------------
    @app.before_request
    def require_login():
        if not cfg.ADMIN_PASSWORD:
            return None
        auth = request.authorization
        if auth and hmac.compare_digest(auth.username or "", cfg.ADMIN_USER) and \
                hmac.compare_digest(auth.password or "", cfg.ADMIN_PASSWORD):
            return None
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="SiteGuard"'})

    def error(msg, code=400):
        return jsonify({"ok": False, "status": msg}), code

    def read_image(file_storage):
        data = np.frombuffer(file_storage.read(), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise EnrolmentError("Uploaded file is not a valid image.")
        return img

    def enrol_sample(person_id, bgr, force=False):
        """Validate, de-duplicate and store one face sample for a person."""
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        enc = encode_single_face(rgb)
        if not force:
            gallery, meta = db.load_gallery()
            if gallery:
                dists = face_recognition.face_distance(np.array(gallery), enc)
                best = int(np.argmin(dists))
                if dists[best] <= cfg.MATCH_TOLERANCE and meta[best]["id"] != person_id:
                    raise EnrolmentError(
                        f"This face already matches '{meta[best]['name']}' ({meta[best]['category']}). "
                        "Duplicate enrolment blocked.")
        filename = f"person_{person_id}_{uuid.uuid4().hex[:8]}.jpg"
        cv2.imwrite(os.path.join(cfg.FACES_DIR, filename), bgr)
        db.add_sample(person_id, enc, filename)

    # ---- pages ------------------------------------------------------------
    @app.route("/")
    def index():
        return render_template("index.html", cfg=cfg, categories=CATEGORIES,
                               projects=PROJECTS, severities=SEVERITIES)

    @app.route("/video_feed")
    def video_feed():
        return Response(engine.mjpeg(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/snapshots/<path:filename>")
    def snapshot(filename):
        return send_from_directory(cfg.SNAPSHOT_DIR, filename)

    @app.route("/faces/<path:filename>")
    def face_image(filename):
        return send_from_directory(cfg.FACES_DIR, filename)

    # ---- system -----------------------------------------------------------
    @app.route("/api/status")
    def api_status():
        today = date.today().isoformat()
        return jsonify({
            "engine": engine.status(),
            "stats": db.stats(today),
            "on_site": db.on_site_now(cfg.ON_SITE_WINDOW_MIN),
            "alerts": db.list_events(unacked_only=True, limit=20),
            "site": {"name": cfg.SITE_NAME, "project": cfg.PROJECT_NAME,
                     "work_hours": [cfg.WORK_START_HOUR, cfg.WORK_END_HOUR]},
        })

    @app.route("/api/security/start", methods=["POST"])
    @app.route("/start_security", methods=["POST"])
    def start_security():
        try:
            started = engine.start()
        except RuntimeError as exc:
            return error(str(exc), 503)
        return jsonify({"ok": True, "status": "Security armed" if started else "Already running"})

    @app.route("/api/security/stop", methods=["POST"])
    @app.route("/stop_security", methods=["POST"])
    def stop_security():
        engine.stop()
        return jsonify({"ok": True, "status": "Security disarmed"})

    # ---- people -----------------------------------------------------------
    @app.route("/api/persons")
    def list_persons():
        return jsonify(db.list_persons())

    @app.route("/api/persons", methods=["POST"])
    @app.route("/capture_image", methods=["POST"])
    def add_person():
        name = (request.form.get("name") or "").strip()
        category = request.form.get("category") or "Staff"
        if not name:
            return error("Name is required")
        if len(name) > 80:
            return error("Name is too long")
        if category not in CATEGORIES:
            return error("Invalid category")
        images = request.files.getlist("image")
        use_live = request.form.get("source") == "live"
        if not images and not use_live:
            return error("At least one photo is required")
        try:
            frames = [read_image(f) for f in images]
            if use_live:
                rgb = engine.latest_rgb()
                if rgb is None:
                    return error("Security camera is not running")
                frames.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        except EnrolmentError as exc:
            return error(str(exc))

        person_id = db.add_person(
            name, category, request.form.get("project", ""), request.form.get("phone", ""),
            request.form.get("notes", ""), request.form.get("valid_until") or None)
        added, problems = 0, []
        for i, bgr in enumerate(frames, 1):
            try:
                enrol_sample(person_id, bgr, force=request.form.get("force") == "1")
                added += 1
            except EnrolmentError as exc:
                problems.append(f"Photo {i}: {exc}")
            except RuntimeError as exc:
                problems.append(str(exc))
                break
        if not added:
            db.delete_person(person_id)
            return error(" ".join(problems) or "Enrolment failed", 422)
        engine.reload_gallery()
        msg = f"{name} enrolled as {category} with {added} face sample(s)."
        if problems:
            msg += " Skipped: " + " ".join(problems)
        return jsonify({"ok": True, "status": msg, "id": person_id})

    @app.route("/api/persons/<int:person_id>/samples", methods=["POST"])
    def add_samples(person_id):
        if not db.get_person(person_id):
            abort(404)
        added, problems = 0, []
        for i, f in enumerate(request.files.getlist("image"), 1):
            try:
                enrol_sample(person_id, read_image(f))
                added += 1
            except (EnrolmentError, RuntimeError) as exc:
                problems.append(f"Photo {i}: {exc}")
        engine.reload_gallery()
        return jsonify({"ok": added > 0, "status": f"{added} sample(s) added. " + " ".join(problems)})

    @app.route("/api/persons/<int:person_id>", methods=["PATCH"])
    def update_person(person_id):
        if not db.get_person(person_id):
            abort(404)
        data = request.get_json(silent=True) or {}
        if "category" in data and data["category"] not in CATEGORIES:
            return error("Invalid category")
        db.update_person(person_id, **data)
        engine.reload_gallery()
        return jsonify({"ok": True, "status": "Updated"})

    @app.route("/api/persons/<int:person_id>", methods=["DELETE"])
    def delete_person(person_id):
        for fname in db.delete_person(person_id):
            path = os.path.join(cfg.FACES_DIR, os.path.basename(fname))
            if os.path.exists(path):
                os.remove(path)
        engine.reload_gallery()
        return jsonify({"ok": True, "status": "Person removed"})

    # ---- events & register ------------------------------------------------
    @app.route("/api/events")
    def list_events():
        return jsonify(db.list_events(
            date=request.args.get("date") or None,
            severity=request.args.get("severity") or None,
            limit=min(request.args.get("limit", 200, type=int) or 200, 1000)))

    @app.route("/api/events/<int:event_id>/ack", methods=["POST"])
    def ack_event(event_id):
        note = ((request.get_json(silent=True) or {}).get("note") or "")[:300]
        db.ack_event(event_id, note)
        return jsonify({"ok": True, "status": "Acknowledged"})

    @app.route("/api/register")
    def register():
        day = request.args.get("date") or date.today().isoformat()
        return jsonify({"date": day, "rows": db.site_register(day), "hourly": db.hourly_counts(day)})

    @app.route("/api/register.csv")
    def register_csv():
        day = request.args.get("date") or date.today().isoformat()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "Name", "Category", "Project", "First In", "Last Seen", "Hours On Site", "Sightings"])
        for r in db.site_register(day):
            w.writerow([day, r["name"], r["category"], r["project"], r["first_in"][11:16],
                        r["last_seen"][11:16], r["hours_on_site"], r["sightings"]])
        return Response(buf.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition": f"attachment; filename=site_register_{day}.csv"})

    return app


if __name__ == "__main__":
    create_app().run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG, threaded=True)
