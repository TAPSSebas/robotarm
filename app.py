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
    "target_shape": "cube",
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

# --- Camera & detection ---
from picamera2 import Picamera2
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(
    main={"format": "RGB888", "size": (640, 480)}
))
picam2.start()

COLOR_RANGES = {
    "red":    [((0,120,70),   (10,255,255)),
               ((170,120,70), (180,255,255))],
    "blue":   [((100,120,70), (130,255,255))],
    "green":  [((40,70,70),   (80,255,255))],
    "yellow": [((20,100,100), (35,255,255))],
}

def detect_objects(frame, color):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = None
    for lo, hi in COLOR_RANGES.get(color, []):
        m = cv2.inRange(hsv, np.array(lo), np.array(hi))
        mask = m if mask is None else mask | m
    if mask is None:
        return []
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [c for c in contours if cv2.contourArea(c) > 1000]

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

def generate_video():
    while True:
        with frame_lock:
            if latest_frame is None:
                time.sleep(0.05)
                continue
            frame = latest_frame.copy()

        color = state['target_color']
        contours = detect_objects(frame, color)
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)
            cv2.putText(frame, color, (x, y-8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

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
                continue
            frame = latest_frame.copy()
        contours = detect_objects(frame, state['target_color'])
        if contours and time.time() - last_pick > 3:
            c = max(contours, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(c)
            cx, cy = x + w//2, y + h//2
            send_arduino(f"PICK {cx} {cy}")
            socketio.emit('log', {'msg': f"detected {state['target_color']} at {cx},{cy} — picking", 'type': 'ok'})
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
    socketio.emit('log', {'msg': f"target: {state['target_color']} {state['target_shape']}", 'type': 'ok'})
    return jsonify(ok=True)

@app.route('/api/pause', methods=['POST'])
def pause():
    state['paused'] = not state['paused']
    send_arduino("STOP" if state['paused'] else "RESUME")
    socketio.emit('log', {'msg': 'emergency stop' if state['paused'] else 'resumed', 'type': 'err' if state['paused'] else 'ok'})
    return jsonify(paused=state['paused'])

@app.route('/api/auto', methods=['POST'])
def auto():
    state['auto_mode'] = not state['auto_mode']
    socketio.emit('log', {'msg': f"auto mode {'on' if state['auto_mode'] else 'off'}", 'type': 'ok'})
    return jsonify(auto=state['auto_mode'])

@app.route('/api/jog', methods=['POST'])
def jog():
    if state['paused']:
        return jsonify(ok=False)
    d = request.json
    send_arduino(f"JOG {d['direction']} {state['speed']}")
    socketio.emit('log', {'msg': f"jog {d['direction']}", 'type': 'ok'})
    return jsonify(ok=True)

@app.route('/api/gripper', methods=['POST'])
def gripper():
    if state['paused']:
        return jsonify(ok=False)
    action = request.json.get('action')
    send_arduino(f"GRIPPER {action}")
    socketio.emit('log', {'msg': f"gripper {action}", 'type': 'ok'})
    return jsonify(ok=True)

@app.route('/api/speed', methods=['POST'])
def speed():
    state['speed'] = int(request.json.get('speed', 50))
    send_arduino(f"SPEED {state['speed']}")
    return jsonify(ok=True)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=80, debug=False)
