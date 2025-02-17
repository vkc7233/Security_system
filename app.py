from flask import Flask, render_template, request, jsonify
import os
import cv2
import face_recognition
import sqlite3
import numpy as np
import threading
import requests

app = Flask(__name__)

# Security status
security_active = False

# Telegram Bot Configuration
BOT_TOKEN = "7646893440:AAGP5QCWLgJYZgXExtE19cuXcrc23vWSNSk"  # Replace with your bot token
CHAT_ID = "1582728185"  # Replace with your chat ID

# Database setup
DB_PATH = "faces.db"
KNOWN_FACES_DIR = "known_faces"
os.makedirs(KNOWN_FACES_DIR, exist_ok=True)

def send_telegram_message(message):
    """Sends a text alert to the Telegram bot."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    response = requests.post(url, data=data)
    if response.status_code == 200:
        print("📩 Telegram message sent successfully!")
    else:
        print("❌ Failed to send message:", response.text)

def send_telegram_photo(photo_path):
    """Sends a photo to the Telegram bot."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as photo:
        files = {"photo": photo}
        data = {"chat_id": CHAT_ID}
        response = requests.post(url, data=data, files=files)
    if response.status_code == 200:
        print("📸 Photo sent successfully!")
    else:
        print("❌ Failed to send photo:", response.text)

def add_known_face(name, image_path):
    """Encodes and stores a known face in the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS known_faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            encoding BLOB
        )
    """)

    image = face_recognition.load_image_file(image_path)
    face_encodings = face_recognition.face_encodings(image)

    if face_encodings:
        encoding = face_encodings[0].tobytes()
        cursor.execute("INSERT INTO known_faces (name, encoding) VALUES (?, ?)", (name, encoding))
        conn.commit()
        conn.close()
        return True
    return False

def security_feed():
    """Starts face recognition and sends Telegram alerts if an unknown face is detected."""
    global security_active
    security_active = True

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, encoding FROM known_faces")
    data = cursor.fetchall()
    conn.close()

    known_face_encodings = []
    known_face_names = []

    for name, encoding in data:
        known_face_encodings.append(np.frombuffer(encoding, dtype=np.float64))
        known_face_names.append(name)

    detected_unknown_faces = []
    video_capture = cv2.VideoCapture(0)

    while security_active:
        ret, frame = video_capture.read()
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_frame)
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

        for face_encoding, face_location in zip(face_encodings, face_locations):
            matches = face_recognition.compare_faces(known_face_encodings, face_encoding, tolerance=0.5)
            name = "Unknown"

            if True in matches:
                first_match_index = matches.index(True)
                name = known_face_names[first_match_index]

            top, right, bottom, left = face_location
            color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.putText(frame, name, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)

            if name == "Unknown":
                is_new_person = True
                for detected_face in detected_unknown_faces:
                    if face_recognition.compare_faces([detected_face], face_encoding, tolerance=0.6)[0]:
                        is_new_person = False
                        break

                if is_new_person:
                    detected_unknown_faces.append(face_encoding)
                    image_path = "unknown_face.jpg"
                    cv2.imwrite(image_path, frame)

                    send_telegram_message("🔔 Unknown Face Detected!")
                    send_telegram_photo(image_path)

        cv2.imshow("Security Feed", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    video_capture.release()
    cv2.destroyAllWindows()
    security_active = False

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/start_security", methods=["POST"])
def start_security():
    global security_active
    if not security_active:
        threading.Thread(target=security_feed).start()
        return jsonify({"status": "Security started"})
    return jsonify({"status": "Already running"})

@app.route("/stop_security", methods=["POST"])
def stop_security():
    global security_active
    security_active = False
    return jsonify({"status": "Security stopped"})
@app.route("/capture_image", methods=["POST"])
def capture_image():
    try:
        # Check if name is provided
        name = request.form.get("name")
        if not name:
            print("❌ Error: No name provided")
            return jsonify({"status": "Name required"}), 400

        # Check if image is provided
        if "image" not in request.files:
            print("❌ Error: No image received")
            return jsonify({"status": "Image file required"}), 400

        file = request.files["image"]

        # Save the image
        image_path = os.path.join(KNOWN_FACES_DIR, f"{name}.jpg")
        file.save(image_path)
        print(f"✅ Image saved at {image_path}")

        # Encode and store the face
        if add_known_face(name, image_path):
            return jsonify({"status": f"{name} added successfully!"})
        return jsonify({"status": "Failed to add person"}), 400

    except Exception as e:
        print(f"❌ Server Error: {e}")
        return jsonify({"status": f"Server error: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True)
