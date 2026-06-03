from flask import Flask, render_template, Response, jsonify, request
from flask_socketio import SocketIO, emit
import cv2
import numpy as np
import serial
import threading
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'robotarm'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# --- State ---
state = {
    "paused": False,
    "target_color": "red",
    "target_shape": "any",
    "speed": 50,
    "auto_mode": False,
}

# --- Arduino ---
try:
    arduino = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
    time.sleep(2)
    print("Arduino connected")
except:
    arduino = None
    print("Arduino not found — running without arm")

def send_arduino(cmd):
    if arduino:
        arduino.write((cmd + '\n').encode())
        print(f"→ Arduino: {cmd}")

# --- Camera ---
from picamera2 import Picamera2
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(
    main={"format": "RGB888", "size": (640, 480)}
))
picam2.start()

# --- Color ranges (HSV) ---
COLOR_RANGES = {
    "red":    [((0,   120, 70),  (10,  255, 255)),
               ((170, 120, 70),  (180, 255, 255))],
    "blue":   [((100, 120, 70),  (130, 255, 255))],
    "green":  [((40,  70,  70),  (80,  255, 255))],
    "yellow": [((20,  100, 100), (35,  255, 255))],
}

# --- Shape detection ---
def classify_shape(contour):
    """
    Classify a contour as cube, sphere, cylinder, or unknown.

    Rules:
      sphere   — circularity > 0.82
      cube     — 4 vertices, aspect ratio 0.8–1.25  (square-ish box)
      cylinder — 4 vertices, aspect ratio outside cube range (tall/wide rect)
                 OR high vertex count with low circularity (oval top-view)
      any      — fallback, matches everything
    """
    area = cv2.contourArea(contour)
    if area < 100:
        return "unknown"

    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.04 * peri, True)
    vertices = len(approx)

    # Circularity: 1.0 = perfect circle
    circularity = (4 * np.pi * area / (peri * peri)) if peri > 0 else 0

    x, y, w, h = cv2.boundingRect(contour)
    aspect = w / float(h) if h > 0 else 1.0

    if circularity > 0.82:
        return "sphere"
    elif vertices == 4 and 0.80 <= aspect <= 1.25:
        return "cube"
    elif vertices == 4 or (vertices > 4 and circularity < 0.75):
        return "cylinder"
    else:
        return "unknown"


def shape_matches(contour, target_shape):
    """Return True if contour matches the requested shape (or target is 'any')."""
    if target_shape == "any":
        return True
    detected = classify_shape(contour)
    return detected == target_shape


# --- Object detection (color + shape) ---
def detect_objects(frame, color, shape="any"):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    mask = None
    for lo, hi in COLOR_RANGES.get(color, []):
        m = cv2.inRange(hsv, np.array(lo), np.array(hi))
        mask = m if mask is None else mask | m
    if mask is None:
        return []

    # Morphological clean-up: remove noise, fill gaps
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    result = []
    for c in contours:
        if cv2.contourArea(c) < 1000:
            continue
        if shape_matches(c, shape):
            result.append(c)
    return result


# --- Frame capture loop ---
frame_lock = threading.Lock()
latest_frame = None

def capture_loop():
    global latest_frame
    while True:
        frame = picam2.capture_array()
        with frame_lock:
            latest_frame = frame.copy()
        time.sleep(0.03)

threading.Thread(target=capture_loop, daemon=True).start()


# --- MJPEG video stream ---
def generate_video():
    while True:
        with frame_lock:
            if latest_frame is None:
                time.sleep(0.05)
                continue
            frame = latest_frame.copy()

        color = state['target_color']
        shape = state['target_shape']
        contours = detect_objects(frame, color, shape)

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            detected_shape = classify_shape(cnt)
            label = f"{color} {detected_shape}"
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
            cv2.putText(frame, label, (x, y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
            # Draw centre cross
            cx, cy = x + w // 2, y + h // 2
            cv2.drawMarker(frame, (cx, cy), (0, 255, 0),
                           cv2.MARKER_CROSS, 12, 2)

        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
               + buf.tobytes() + b'\r\n')
        time.sleep(0.04)


# --- Vision auto-pick loop ---
def vision_loop():
    last_pick = 0
    while True:
        if not state['auto_mode'] or state['paused']:
            time.sleep(0.1)
            continue

        with frame_lock:
            if latest_frame is None:
                time.sleep(0.1)
                continue
            frame = latest_frame.copy()

        color = state['target_color']
        shape = state['target_shape']
        contours = detect_objects(frame, color, shape)

        if contours and (time.time() - last_pick) > 4:
            # Pick the largest matching object
            c = max(contours, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(c)
            cx, cy = x + w // 2, y + h // 2
            detected_shape = classify_shape(c)

            send_arduino(f"PICK {cx} {cy}")
            socketio.emit('log', {
                'msg': f"detected {color} {detected_shape} at ({cx},{cy}) — picking",
                'type': 'ok'
            })
            last_pick = time.time()

        time.sleep(0.1)

threading.Thread(target=vision_loop, daemon=True).start()


# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video')
def video():
    return Response(generate_video(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/target', methods=['POST'])
def set_target():
    d = request.json
    state['target_color'] = d.get('color', state['target_color'])
    state['target_shape'] = d.get('shape', state['target_shape'])
    socketio.emit('log', {
        'msg': f"target: {state['target_color']} {state['target_shape']}",
        'type': 'ok'
    })
    return jsonify(ok=True)

@app.route('/api/pause', methods=['POST'])
def pause():
    state['paused'] = not state['paused']
    send_arduino("STOP" if state['paused'] else "RESUME")
    socketio.emit('log', {
        'msg': 'emergency stop' if state['paused'] else 'resumed',
        'type': 'err' if state['paused'] else 'ok'
    })
    return jsonify(paused=state['paused'])

@app.route('/api/auto', methods=['POST'])
def auto():
    state['auto_mode'] = not state['auto_mode']
    socketio.emit('log', {
        'msg': f"auto mode {'on' if state['auto_mode'] else 'off'}",
        'type': 'ok'
    })
    return jsonify(auto=state['auto_mode'])

@app.route('/api/jog', methods=['POST'])
def jog():
    if state['paused']:
        return jsonify(ok=False, reason='paused')
    d = request.json
    send_arduino(f"JOG {d['direction']} {state['speed']}")
    socketio.emit('log', {'msg': f"jog {d['direction']}", 'type': 'ok'})
    return jsonify(ok=True)

@app.route('/api/gripper', methods=['POST'])
def gripper():
    if state['paused']:
        return jsonify(ok=False, reason='paused')
    action = request.json.get('action')
    send_arduino(f"GRIPPER {action}")
    socketio.emit('log', {'msg': f"gripper {action}", 'type': 'ok'})
    return jsonify(ok=True)

@app.route('/api/speed', methods=['POST'])
def speed():
    state['speed'] = int(request.json.get('speed', 50))
    send_arduino(f"SPEED {state['speed']}")
    return jsonify(ok=True)

@app.route('/api/home', methods=['POST'])
def home():
    if state['paused']:
        return jsonify(ok=False, reason='paused')
    send_arduino("HOME")
    socketio.emit('log', {'msg': 'moving to home position', 'type': 'ok'})
    return jsonify(ok=True)

@app.route('/api/status')
def status():
    return jsonify(state)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=80, debug=False)
