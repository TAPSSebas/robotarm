# RobotArm — EEZYbotARM MK3 with Raspberry Pi Vision Control

A Raspberry Pi powered robot arm controller with live camera feed, color & shape based object detection, and a web UI accessible from any device on your network. Built around the EEZYbotARM MK3 and an Arduino Uno.

---

## What it does

- Streams a live camera feed to a web browser
- Detects objects by color (red, blue, green, yellow) and shape using OpenCV
- Automatically picks up target objects and ignores others
- Full manual control via web UI — jog, gripper open/close, speed
- Emergency stop button
- Works from any phone or laptop on the same WiFi network

---

## Hardware

- Raspberry Pi 4 (2GB+ recommended)
- Raspberry Pi Camera Module v2
- Arduino Uno
- [EEZYbotARM MK3](https://www.thingiverse.com/thing:2838859) (3D printed)
- USB cable (Pi to Arduino)
- Motors/drivers (see Arduino section)

---

## Project structure

```
robotarm/
├── app.py              # Flask server — runs on the Raspberry Pi
└── templates/
    └── index.html      # Web UI — open in any browser
```

---

## Part 1 — Raspberry Pi setup

### Step 1 — Flash Raspberry Pi OS

Download and install **Raspberry Pi Imager** from [raspberrypi.com/software](https://www.raspberrypi.com/software/).

Flash **Raspberry Pi OS Lite (64-bit)**. Before writing, click the gear icon and configure:
- Hostname: `raspberrypi.local`
- Enable SSH
- Set username: `pi` and a password
- Configure your WiFi network (SSID and password)

### Step 2 — SSH into the Pi

On your laptop, open a terminal:

```bash
ssh pi@raspberrypi.local
```

If that doesn't work, find the Pi's IP from your router's device list and use:

```bash
ssh pi@192.168.1.xxx
```

### Step 3 — Update the Pi

```bash
sudo apt update && sudo apt upgrade -y
```

### Step 4 — Install dependencies

```bash
sudo apt install -y python3-pip python3-opencv libatlas-base-dev git
sudo pip3 install flask flask-socketio pyserial --break-system-packages
sudo pip3 install opencv-python-headless --break-system-packages
sudo apt install -y python3-libcamera python3-picamera2
```

### Step 5 — Set a static IP

So the Pi always has the same address on your network:

```bash
sudo nano /etc/dhcpcd.conf
```

Add at the very bottom (adjust IPs to match your network):

```
interface wlan0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1
```

> To find your router IP run: `ip route | grep default` — it's the number after "via"

Save with `Ctrl+X` → `Y` → `Enter`, then reboot:

```bash
sudo reboot
```

### Step 6 — Enable the camera

```bash
sudo raspi-config
```

Go to **Interface Options** → **Camera** → Enable → reboot.

Or manually check that `/boot/config.txt` contains:

```
camera_auto_detect=1
```

### Step 7 — Clone the repo

```bash
git clone https://github.com/TAPSSebas/robotarm.git
cd robotarm
```

### Step 8 — Run the app

```bash
sudo python3 app.py
```

You should see:
```
* Running on http://0.0.0.0:80
```

### Step 9 — Open the UI

On any device connected to the same WiFi, open a browser and go to:

```
http://192.168.1.100
```

You should see the control panel with a live camera feed.

### Step 10 — Auto-start on boot

So the server starts automatically every time the Pi powers on:

```bash
sudo nano /etc/systemd/system/robotarm.service
```

Paste:

```ini
[Unit]
Description=Robot Arm Control Server
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/robotarm/app.py
WorkingDirectory=/home/pi/robotarm
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

Save, then enable:

```bash
sudo systemctl enable robotarm
sudo systemctl start robotarm
```

From now on the server starts automatically on every boot.

---

## Part 2 — How the vision system works

The Pi captures frames from the camera using `picamera2` and runs color detection using OpenCV.

Objects are detected by converting the frame to HSV color space and applying a color mask. HSV is used instead of RGB because it is much more reliable under different lighting conditions.

### Color ranges (HSV)

| Color  | Lower bound     | Upper bound      |
|--------|-----------------|------------------|
| Red    | (0, 120, 70)    | (10, 255, 255)   |
| Red*   | (170, 120, 70)  | (180, 255, 255)  |
| Blue   | (100, 120, 70)  | (130, 255, 255)  |
| Green  | (40, 70, 70)    | (80, 255, 255)   |
| Yellow | (20, 100, 100)  | (35, 255, 255)   |

*Red wraps around in HSV so it needs two ranges.

When a matching object is found with a contour area over 1000px (to filter out noise), a bounding box is drawn on the video feed and the center coordinates are sent to the Arduino as a `PICK x y` command.

---

## Part 3 — Serial commands (Pi → Arduino)

The Pi communicates with the Arduino over USB serial at 9600 baud. These are the commands the Pi sends:

| Command         | Description                          |
|-----------------|--------------------------------------|
| `PICK x y`      | Pick up object at pixel position x,y |
| `JOG dir speed` | Manually jog the arm (up/down/left/right/forward/back) |
| `GRIPPER open`  | Open the gripper                     |
| `GRIPPER close` | Close the gripper                    |
| `SPEED value`   | Set movement speed (1–100)           |
| `STOP`          | Emergency stop                       |
| `RESUME`        | Resume after stop                    |

---

## Part 4 — Arduino setup

> 🚧 Coming soon — Arduino sketch for motor control based on the EEZYbotARM MK3 hardware.

---

## Part 5 — Calibration

> 🚧 Coming soon — mapping camera pixel coordinates to arm positions.

---

## Troubleshooting

**Camera feed not showing in browser**
- Go to `http://192.168.1.100/video` directly — if it works there, use the full IP in the img src
- Make sure the camera is enabled in `raspi-config`

**cv2 can't open camera (returns False)**
- The Pi uses `libcamera` by default — make sure `picamera2` is installed and the app uses it instead of `cv2.VideoCapture`

**ModuleNotFoundError**
- Install missing modules with `sudo pip3 install <module> --break-system-packages` (sudo is needed because the app runs as root)

**Static IP not working**
- Double check your router IP with `ip route | grep default`
- Make sure the static IP you chose isn't already taken by another device

---

## Updating the code

After making changes on the Pi:

```bash
cd ~/robotarm
git add .
git commit -m "describe what you changed"
git push
```

---

## Credits

- [EEZYbotARM MK3](https://www.thingiverse.com/thing:2838859) by daGHIZmo
- OpenCV for computer vision
- Flask + SocketIO for the web server
- Picamera2 for Raspberry Pi camera access
