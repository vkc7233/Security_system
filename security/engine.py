"""Camera + face recognition engine running in a background thread.

Frames are annotated in-process and served to the browser as an MJPEG stream,
so the system runs headless (no cv2.imshow window) on a site PC or server.
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import cv2
import numpy as np

try:
    import face_recognition
except ImportError:  # allows the dashboard/tests to run without dlib installed
    face_recognition = None

log = logging.getLogger(__name__)

COLORS = {  # BGR
    "Low": (80, 200, 120),
    "Medium": (0, 170, 255),
    "High": (0, 90, 255),
    "Critical": (40, 40, 230),
}


class EnrolmentError(ValueError):
    pass


def require_face_lib():
    if face_recognition is None:
        raise RuntimeError("face_recognition is not installed. Run: pip install -r requirements.txt")


def encode_single_face(rgb_image):
    """Return the encoding of exactly one face, or raise EnrolmentError with a usable message."""
    require_face_lib()
    locations = face_recognition.face_locations(rgb_image)
    if not locations:
        raise EnrolmentError("No face found. Face the camera directly in good light.")
    if len(locations) > 1:
        raise EnrolmentError(f"{len(locations)} faces found. Only the person being enrolled should be in frame.")
    top, right, bottom, left = locations[0]
    if min(bottom - top, right - left) < 60:
        raise EnrolmentError("Face is too small. Move closer to the camera.")
    return face_recognition.face_encodings(rgb_image, locations)[0]


def classify(meta, now, cfg):
    """Rule engine: map a detection to (category, severity, reason)."""
    after_hours = not (cfg.WORK_START_HOUR <= now.hour < cfg.WORK_END_HOUR)
    if meta is None:
        if after_hours:
            return "Unknown", "High", "Unknown person outside working hours"
        return "Unknown", "Medium", "Unknown person detected"
    cat = meta["category"]
    if cat == "Blacklisted":
        return cat, "Critical", "Blacklisted person detected"
    if meta.get("valid_until"):
        try:
            if now.date() > datetime.fromisoformat(meta["valid_until"]).date():
                return cat, "High", f"Access pass expired on {meta['valid_until']}"
        except ValueError:
            pass
    if after_hours and cat in ("Labour", "Visitor", "Vendor"):
        return cat, "Medium", f"{cat} on site outside working hours"
    return cat, "Low", "Entry recorded"


class SecurityEngine:
    def __init__(self, db, notifier, cfg):
        self.db = db
        self.notifier = notifier
        self.cfg = cfg
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.armed = False
        self.camera_ok = False
        self.started_at = None
        self.fps = 0.0
        self.faces_in_frame = 0
        self.last_error = ""
        self._jpeg = None
        self._raw = None
        self._gallery_enc = np.empty((0, 128))
        self._gallery_meta = []
        self._known_seen = {}   # person_id -> datetime last logged
        self._unknown_seen = []  # [(encoding, datetime)]
        self.reload_gallery()

    # ---- lifecycle -----------------------------------------------------
    def reload_gallery(self):
        enc, meta = self.db.load_gallery()
        with self._lock:
            self._gallery_enc = np.array(enc) if enc else np.empty((0, 128))
            self._gallery_meta = meta

    def start(self):
        require_face_lib()
        if self._thread and self._thread.is_alive():
            if not self._stop.is_set():
                return False  # already running
            self._thread.join(timeout=5)  # previous run still shutting down
            if self._thread.is_alive():
                return False
        self._stop.clear()
        self.armed = True
        self.started_at = datetime.now()
        self._thread = threading.Thread(target=self._run, daemon=True, name="security-engine")
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        self.armed = False
        return True

    def status(self):
        return {
            "armed": self.armed,
            "camera_ok": self.camera_ok,
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at and self.armed else None,
            "fps": round(self.fps, 1),
            "faces_in_frame": self.faces_in_frame,
            "gallery_size": len(self._gallery_meta),
            "last_error": self.last_error,
            "face_lib": face_recognition is not None,
            "telegram": self.notifier.enabled,
        }

    def latest_rgb(self):
        with self._lock:
            return None if self._raw is None else cv2.cvtColor(self._raw, cv2.COLOR_BGR2RGB)

    # ---- streaming -----------------------------------------------------
    def mjpeg(self):
        while True:
            with self._lock:
                jpeg = self._jpeg if self.armed else None
            if jpeg is None:
                jpeg = self._placeholder()
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(0.1 if self.armed else 1.0)

    def _placeholder(self):
        img = np.full((360, 640, 3), 24, dtype=np.uint8)
        msg = "SYSTEM DISARMED" if not self.armed else "CONNECTING TO CAMERA..."
        cv2.putText(img, msg, (150, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (160, 160, 160), 2)
        cv2.putText(img, f"{self.cfg.PROJECT_NAME} | {self.cfg.SITE_NAME}", (150, 215),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (110, 110, 110), 1)
        return cv2.imencode(".jpg", img)[1].tobytes()

    # ---- main loop -----------------------------------------------------
    def _open_camera(self):
        src = self.cfg.CAMERA_SOURCE
        cap = cv2.VideoCapture(int(src) if str(src).isdigit() else src)
        return cap if cap.isOpened() else None

    def _run(self):
        cap, backoff, frame_no, t_last = None, 1, 0, time.time()
        offline_alerted = False
        last_boxes = []
        try:
            while not self._stop.is_set():
                if cap is None:
                    cap = self._open_camera()
                    if cap is None:
                        self.camera_ok = False
                        self.last_error = f"Camera '{self.cfg.CAMERA_SOURCE}' unavailable, retrying"
                        if not offline_alerted:
                            self._raise_system_alert("Camera offline / tampered")
                            offline_alerted = True
                        self._stop.wait(backoff)
                        backoff = min(backoff * 2, 30)
                        continue
                    backoff, offline_alerted = 1, False
                    self.camera_ok, self.last_error = True, ""

                ok, frame = cap.read()
                if not ok or frame is None:
                    cap.release()
                    cap = None
                    continue

                frame_no += 1
                if frame_no % max(1, self.cfg.PROCESS_EVERY_N_FRAMES) == 0:
                    last_boxes = self._process(frame)
                annotated = self._annotate(frame.copy(), last_boxes)
                ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
                with self._lock:
                    self._raw = frame
                    if ok:
                        self._jpeg = buf.tobytes()

                now = time.time()
                self.fps = 0.9 * self.fps + 0.1 * (1.0 / max(now - t_last, 1e-3))
                t_last = now
        except Exception as exc:
            log.exception("Engine crashed")
            self.last_error = f"Engine error: {exc}"
        finally:
            if cap is not None:
                cap.release()
            self.armed = False
            self.camera_ok = False
            with self._lock:
                self._jpeg = None

    def _process(self, frame):
        scale = self.cfg.FRAME_SCALE
        small = cv2.resize(frame, (0, 0), fx=scale, fy=scale) if scale != 1 else frame
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb)
        encodings = face_recognition.face_encodings(rgb, locations)
        self.faces_in_frame = len(locations)
        now = datetime.now()
        boxes = []
        with self._lock:
            gallery, metas = self._gallery_enc, self._gallery_meta
        for enc, (top, right, bottom, left) in zip(encodings, locations):
            meta, confidence = None, None
            if len(gallery):
                dists = face_recognition.face_distance(gallery, enc)
                best = int(np.argmin(dists))
                if dists[best] <= self.cfg.MATCH_TOLERANCE:
                    meta, confidence = metas[best], float(1 - dists[best])
            category, severity, reason = classify(meta, now, self.cfg)
            box = tuple(int(v / scale) for v in (top, right, bottom, left))
            label = meta["name"] if meta else "UNKNOWN"
            boxes.append((box, label, category, severity, confidence))
            if self._should_log(meta, enc, now):
                self._log_event(frame, box, meta, category, severity, reason, confidence, now)
        return boxes

    def _should_log(self, meta, enc, now):
        if meta is not None:
            last = self._known_seen.get(meta["id"])
            if last and now - last < timedelta(minutes=self.cfg.KNOWN_COOLDOWN_MIN):
                return False
            self._known_seen[meta["id"]] = now
            return True
        window = timedelta(minutes=self.cfg.UNKNOWN_COOLDOWN_MIN)
        self._unknown_seen = [(e, t) for e, t in self._unknown_seen if now - t < window]
        if self._unknown_seen:
            dists = face_recognition.face_distance(np.array([e for e, _ in self._unknown_seen]), enc)
            if float(np.min(dists)) <= 0.6:
                return False
        self._unknown_seen.append((enc, now))
        return True

    def _log_event(self, frame, box, meta, category, severity, reason, confidence, now):
        snap = self._save_snapshot(frame, box, severity, now)
        name = meta["name"] if meta else "Unknown"
        self.db.add_event(
            ts=now.isoformat(timespec="seconds"), person_id=meta["id"] if meta else None,
            name=name, category=category, severity=severity, reason=reason,
            site=self.cfg.SITE_NAME, confidence=confidence, snapshot=snap,
        )
        if severity != "Low":
            text = (f"[{severity.upper()}] {reason}\n"
                    f"Person: {name} ({category})\n"
                    f"Site: {self.cfg.PROJECT_NAME} - {self.cfg.SITE_NAME}\n"
                    f"Time: {now.strftime('%d %B %Y %H:%M:%S')}")
            path = os.path.join(self.cfg.SNAPSHOT_DIR, snap) if snap else None
            self.notifier.send(text, path)

    def _save_snapshot(self, frame, box, severity, now):
        rel = os.path.join(now.strftime("%Y-%m-%d"), f"{now.strftime('%H%M%S')}_{severity.lower()}_{int(time.time()*1000)%1000}.jpg")
        path = os.path.join(self.cfg.SNAPSHOT_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img = self._annotate(frame.copy(), [(box, "", "", severity, None)])
        cv2.imwrite(path, img)
        return rel.replace(os.sep, "/")

    def _raise_system_alert(self, reason):
        self.db.add_event(name="System", category="System", severity="High", reason=reason,
                          site=self.cfg.SITE_NAME)
        self.notifier.send(f"[HIGH] {reason}\nSite: {self.cfg.PROJECT_NAME} - {self.cfg.SITE_NAME}")

    def _annotate(self, frame, boxes):
        for (top, right, bottom, left), label, category, severity, conf in boxes:
            color = COLORS.get(severity, (200, 200, 200))
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            if label:
                text = f"{label} | {category}" + (f" {conf*100:.0f}%" if conf else "")
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
                y = max(top - 8, th + 8)
                cv2.rectangle(frame, (left, y - th - 6), (left + tw + 8, y + 4), color, -1)
                cv2.putText(frame, text, (left + 4, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        hud = f"{self.cfg.SITE_NAME}  {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}  ARMED"
        cv2.putText(frame, hud, (10, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(frame, hud, (10, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        return frame
