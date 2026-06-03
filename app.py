from flask import Flask, render_template, Response, jsonify, request
from flask_socketio import SocketIO, emit
import cv2
import numpy as np
import serial
import threading
import time
import os
import configparser
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'robotarm'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ============================================================
#  Config laden
# ============================================================
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.txt')

def load_config():
    cfg = configparser.ConfigParser(inline_comment_prefixes=('#',))
    cfg.read_string('[main]\n' + open(CONFIG_FILE).read())
    s = cfg['main']

    camera_height   = float(s.get('camera_height',  '30').strip())
    workspace_width = float(s.get('workspace_width', '40').strip())
    workspace_depth = float(s.get('workspace_depth', '35').strip())

    bins = {}
    for key, val in s.items():
        if key.startswith('bin_'):
            parts = [int(v.strip()) for v in val.split(',')]
            bins[key[4:]] = tuple(parts)   # e.g. "red_any" -> (170,80,100)

    return {
        'camera_height':   camera_height,
        'workspace_width': workspace_width,
        'workspace_depth': workspace_depth,
        'bins': bins,
    }

config = load_config()

def get_bin(color, shape):
    """Return (base, shoulder, elbow) for the best matching bin."""
    b = config['bins']
    for key in [f"{color}_{shape}", f"{color}_any", f"any_{shape}", "default"]:
        if key in b:
            return b[key]
    return (90, 80, 100)

# ============================================================
#  State
# ============================================================
state = {
    "paused":        False,
    "target_color":  "red",
    "target_shape":  "any",
    "speed":         50,
    "auto_mode":     False,
    "sorting":       False,
    "sort_progress": [],   # list of {"color","shape","status"}
}

# ============================================================
#  Arduino
# ============================================================
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

# ============================================================
#  Camera
# ============================================================
try:
    from picamera2 import Picamera2
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(
        main={"format": "RGB888", "size": (640, 480)}
    ))
    picam2.start()
    USE_PICAM = True
except Exception:
    USE_PICAM = False
    _cap = cv2.VideoCapture(0)

# ============================================================
#  Kleur ranges (HSV)
# ============================================================
COLOR_RANGES = {
    "red":    [((0,   120, 70),  (10,  255, 255)),
               ((170, 120, 70),  (180, 255, 255))],
    "blue":   [((100, 120, 70),  (130, 255, 255))],
    "green":  [((40,  70,  70),  (80,  255, 255))],
    "yellow": [((20,  100, 100), (35,  255, 255))],
}

# ============================================================
#  Vorm detectie
# ============================================================
def classify_shape(contour):
    area = cv2.contourArea(contour)
    if area < 100:
        return "unknown"
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.04 * peri, True)
    vertices = len(approx)
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
    if target_shape == "any":
        return True
    return classify_shape(contour) == target_shape

# ============================================================
#  Object detectie (kleur + vorm)
# ============================================================
def detect_objects(frame, color, shape="any"):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = None
    for lo, hi in COLOR_RANGES.get(color, []):
        m = cv2.inRange(hsv, np.array(lo), np.array(hi))
        mask = m if mask is None else mask | m
    if mask is None:
        return []
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [c for c in contours
            if cv2.contourArea(c) >= 1000 and shape_matches(c, shape)]

def detect_all_objects(frame):
    """Detecteer alle objecten van alle kleuren en vormen."""
    found = []
    for color in COLOR_RANGES:
        contours = detect_objects(frame, color, "any")
        for c in contours:
            shape = classify_shape(c)
            x, y, w, h = cv2.boundingRect(c)
            found.append({
                "color": color,
                "shape": shape,
                "cx": x + w // 2,
                "cy": y + h // 2,
                "area": cv2.contourArea(c),
            })
    # Dedup dichtbij-liggende detecties (zelfde object, meerdere kleurranges)
    unique = []
    for obj in found:
        too_close = False
        for u in unique:
            if abs(obj["cx"] - u["cx"]) < 30 and abs(obj["cy"] - u["cy"]) < 30:
                too_close = True
                break
        if not too_close:
            unique.append(obj)
    return unique

# ============================================================
#  Frame capture loop
# ============================================================
frame_lock = threading.Lock()
latest_frame = None

def capture_loop():
    global latest_frame
    while True:
        if USE_PICAM:
            frame = picam2.capture_array()
        else:
            ret, frame = _cap.read()
            if not ret:
                time.sleep(0.05)
                continue
        with frame_lock:
            latest_frame = frame.copy()
        time.sleep(0.03)

threading.Thread(target=capture_loop, daemon=True).start()

# ============================================================
#  MJPEG stream
# ============================================================
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
            cx, cy = x + w // 2, y + h // 2
            cv2.drawMarker(frame, (cx, cy), (0, 255, 0), cv2.MARKER_CROSS, 12, 2)

        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
               + buf.tobytes() + b'\r\n')
        time.sleep(0.04)

# ============================================================
#  Auto pick loop (enkel geselecteerde kleur+vorm)
# ============================================================
def vision_loop():
    last_pick = 0
    while True:
        if not state['auto_mode'] or state['paused'] or state['sorting']:
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
            c = max(contours, key=cv2.contourArea)
            x, y, w, h = cv2.boundingRect(c)
            cx, cy = x + w // 2, y + h // 2
            detected_shape = classify_shape(c)
            drop = get_bin(color, detected_shape)
            send_arduino(f"PICK {cx} {cy} {drop[0]} {drop[1]} {drop[2]}")
            socketio.emit('log', {
                'msg': f"detected {color} {detected_shape} at ({cx},{cy}) → bin {drop}",
                'type': 'ok'
            })
            last_pick = time.time()

        time.sleep(0.1)

threading.Thread(target=vision_loop, daemon=True).start()

# ============================================================
#  SORTEER routine
# ============================================================
def sort_routine():
    state['sorting'] = True
    state['sort_progress'] = []
    socketio.emit('sort_start', {})

    # Scan frame
    with frame_lock:
        if latest_frame is None:
            state['sorting'] = False
            socketio.emit('sort_done', {'items': []})
            return
        frame = latest_frame.copy()

    objects = detect_all_objects(frame)
    socketio.emit('log', {'msg': f"sorteer: {len(objects)} objecten gevonden", 'type': 'ok'})

    results = []
    for i, obj in enumerate(objects):
        if state['paused']:
            socketio.emit('log', {'msg': 'sorteer gestopt door noodstop', 'type': 'err'})
            break

        drop = get_bin(obj['color'], obj['shape'])
        status_item = {
            "index": i + 1,
            "color": obj['color'],
            "shape": obj['shape'],
            "status": "bezig"
        }
        state['sort_progress'].append(status_item)
        socketio.emit('sort_progress', state['sort_progress'])
        socketio.emit('log', {
            'msg': f"[{i+1}/{len(objects)}] pak {obj['color']} {obj['shape']}",
            'type': 'ok'
        })

        send_arduino(f"PICK {obj['cx']} {obj['cy']} {drop[0]} {drop[1]} {drop[2]}")

        # Wacht tot Arduino DONE terugstuur (max 15 sec)
        start = time.time()
        done = False
        while time.time() - start < 15:
            if arduino and arduino.in_waiting:
                line = arduino.readline().decode().strip()
                if line == "DONE":
                    done = True
                    break
            elif arduino is None:
                time.sleep(2)  # Simuleer
                done = True
                break
            time.sleep(0.1)

        status_item['status'] = 'klaar' if done else 'timeout'
        results.append(status_item.copy())
        socketio.emit('sort_progress', state['sort_progress'])

    state['sorting'] = False
    socketio.emit('sort_done', {'items': results})
    socketio.emit('log', {'msg': 'sorteren klaar!', 'type': 'ok'})

# ============================================================
#  Resultaten opslaan
# ============================================================
RESULTS_FILE = os.path.join(os.path.dirname(__file__), 'resultaten.txt')

def save_results(naam, klas, notities, items):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = []
    if not os.path.exists(RESULTS_FILE) or os.path.getsize(RESULTS_FILE) == 0:
        lines.append(f"{'Datum/Tijd':<20} {'Naam':<20} {'Klas':<10} {'Objecten':>8}  Resultaat")
        lines.append('-' * 80)

    # Samenvatting
    totaal  = len(items)
    klaar   = sum(1 for i in items if i['status'] == 'klaar')
    samenvatting = f"{klaar}/{totaal} gesorteerd"
    lines.append(f"{now:<20} {naam:<20} {klas:<10} {totaal:>8}  {samenvatting}")

    # Detail per object
    for item in items:
        lines.append(f"  {'':20} {'':20} {'':10}   #{item['index']:>3}  {item['color']:<8} {item['shape']:<10} {item['status']}")

    if notities:
        lines.append(f"  Notities: {notities}")
    lines.append('')

    with open(RESULTS_FILE, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

# ============================================================
#  Routes
# ============================================================
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

@app.route('/api/sort', methods=['POST'])
def start_sort():
    if state['sorting']:
        return jsonify(ok=False, reason='already sorting')
    if state['paused']:
        return jsonify(ok=False, reason='paused')
    threading.Thread(target=sort_routine, daemon=True).start()
    return jsonify(ok=True)

@app.route('/api/results', methods=['POST'])
def save_results_api():
    d = request.json
    save_results(
        naam=d.get('naam', 'onbekend'),
        klas=d.get('klas', ''),
        notities=d.get('notities', ''),
        items=d.get('items', [])
    )
    socketio.emit('log', {'msg': f"resultaten opgeslagen voor {d.get('naam')}", 'type': 'ok'})
    return jsonify(ok=True)

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

@app.route('/api/reload_config', methods=['POST'])
def reload_config():
    global config
    config = load_config()
    socketio.emit('log', {'msg': 'config.txt herladen', 'type': 'ok'})
    return jsonify(ok=True)

@app.route('/api/status')
def status():
    return jsonify(state)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=80, debug=False)