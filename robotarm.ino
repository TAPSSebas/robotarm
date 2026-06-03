// ============================================================
//  EEZYbotARM MK3 — Arduino Sketch
//  Serial commands from Raspberry Pi @ 9600 baud
//  Servo wiring:
//    Base     → pin 9
//    Shoulder → pin 10
//    Elbow    → pin 11
//    Gripper  → pin 6
// ============================================================

#include <Servo.h>

// --- Servo objects ---
Servo servoBase;
Servo servoShoulder;
Servo servoElbow;
Servo servoGripper;

// --- Servo pins ---
#define PIN_BASE      9
#define PIN_SHOULDER  10
#define PIN_ELBOW     11
#define PIN_GRIPPER   6

// --- Soft limits (degrees) — tune these for your build ---
#define BASE_MIN       10
#define BASE_MAX       170
#define SHOULDER_MIN   40
#define SHOULDER_MAX   140
#define ELBOW_MIN      30
#define ELBOW_MAX      150
#define GRIPPER_OPEN   30
#define GRIPPER_CLOSED 100

// --- Home position ---
#define HOME_BASE      90
#define HOME_SHOULDER  90
#define HOME_ELBOW     90

// --- Drop-off position (where arm places the object) ---
#define DROP_BASE      170
#define DROP_SHOULDER  80
#define DROP_ELBOW     100

// --- State ---
int posBase     = HOME_BASE;
int posShoulder = HOME_SHOULDER;
int posElbow    = HOME_ELBOW;
int posGripper  = GRIPPER_OPEN;
int moveSpeed   = 50;   // 1–100
bool isStopped  = false;

// ============================================================
//  Smooth single-servo move
// ============================================================
void smoothMove(Servo& s, int& current, int target, int minV, int maxV) {
  target = constrain(target, minV, maxV);
  int step = (target >= current) ? 1 : -1;
  // Map speed 1-100 to delay 18-2 ms per degree
  int delayMs = map(moveSpeed, 1, 100, 18, 2);
  while (current != target) {
    current += step;
    s.write(current);
    delay(delayMs);
  }
}

// Shorthand wrappers
void moveBase(int deg)     { smoothMove(servoBase,     posBase,     deg, BASE_MIN,     BASE_MAX);     }
void moveShoulder(int deg) { smoothMove(servoShoulder, posShoulder, deg, SHOULDER_MIN, SHOULDER_MAX); }
void moveElbow(int deg)    { smoothMove(servoElbow,    posElbow,    deg, ELBOW_MIN,    ELBOW_MAX);     }
void moveGripper(int deg)  { posGripper = constrain(deg, GRIPPER_OPEN, GRIPPER_CLOSED);
                             servoGripper.write(posGripper); delay(400); }

// ============================================================
//  Go home
// ============================================================
void goHome() {
  moveShoulder(HOME_SHOULDER);
  moveElbow(HOME_ELBOW);
  moveBase(HOME_BASE);
}

// ============================================================
//  PICK  —  pixel (px, py) from 640×480 camera frame
//
//  Mapping:
//    px 0–640  →  base  160°–20°  (left=far right of arm)
//    py 0–480  →  reach  (top=far, bottom=close)
//
//  Adjust DROP_* constants above to set where objects land.
// ============================================================
void doPick(int px, int py) {
  // --- Map pixel to arm angles ---
  int targetBase     = map(px, 0, 640, 160, 20);
  int targetShoulder = map(py, 0, 480, 120,  55);  // far → raised, close → lower
  int targetElbow    = map(py, 0, 480,  50, 110);

  targetBase     = constrain(targetBase,     BASE_MIN,     BASE_MAX);
  targetShoulder = constrain(targetShoulder, SHOULDER_MIN, SHOULDER_MAX);
  targetElbow    = constrain(targetElbow,    ELBOW_MIN,    ELBOW_MAX);

  // 1. Open gripper
  moveGripper(GRIPPER_OPEN);

  // 2. Rotate base first (arm up)
  moveShoulder(HOME_SHOULDER);
  moveElbow(HOME_ELBOW);
  moveBase(targetBase);

  // 3. Approach — move to position slightly above object
  moveShoulder(targetShoulder + 15);
  moveElbow(targetElbow);

  // 4. Lower onto object
  moveShoulder(targetShoulder);
  delay(150);

  // 5. Grab
  moveGripper(GRIPPER_CLOSED);

  // 6. Lift
  moveShoulder(targetShoulder + 20);

  // 7. Swing to drop-off
  moveBase(DROP_BASE);
  moveShoulder(DROP_SHOULDER);
  moveElbow(DROP_ELBOW);
  delay(200);

  // 8. Release
  moveGripper(GRIPPER_OPEN);
  delay(200);

  // 9. Go home
  goHome();

  Serial.println("DONE");
}

// ============================================================
//  JOG  —  direction + speed
// ============================================================
void doJog(const char* dir, int spd) {
  int step = map(spd, 1, 100, 2, 12);
  String d = String(dir);
  if      (d == "left")    moveBase(posBase + step);
  else if (d == "right")   moveBase(posBase - step);
  else if (d == "forward") moveShoulder(posShoulder - step);
  else if (d == "back")    moveShoulder(posShoulder + step);
  else if (d == "up")      moveElbow(posElbow + step);
  else if (d == "down")    moveElbow(posElbow - step);
  Serial.println("OK");
}

// ============================================================
//  Serial command parser
// ============================================================
void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd.startsWith("PICK") && !isStopped) {
    int px, py;
    if (sscanf(cmd.c_str(), "PICK %d %d", &px, &py) == 2) {
      doPick(px, py);
    }

  } else if (cmd.startsWith("JOG") && !isStopped) {
    char dir[16]; int spd = moveSpeed;
    sscanf(cmd.c_str(), "JOG %15s %d", dir, &spd);
    doJog(dir, spd);

  } else if (cmd.startsWith("GRIPPER") && !isStopped) {
    char action[10];
    sscanf(cmd.c_str(), "GRIPPER %9s", action);
    if (String(action) == "open")  moveGripper(GRIPPER_OPEN);
    else                            moveGripper(GRIPPER_CLOSED);
    Serial.println("OK");

  } else if (cmd.startsWith("SPEED")) {
    int s;
    if (sscanf(cmd.c_str(), "SPEED %d", &s) == 1) {
      moveSpeed = constrain(s, 1, 100);
    }
    Serial.println("OK");

  } else if (cmd == "STOP") {
    isStopped = true;
    // Detach servos so they go limp — prevents damage on hard stop
    servoBase.detach();
    servoShoulder.detach();
    servoElbow.detach();
    servoGripper.detach();
    Serial.println("STOPPED");

  } else if (cmd == "RESUME") {
    isStopped = false;
    // Re-attach and write last known positions
    servoBase.attach(PIN_BASE);         servoBase.write(posBase);
    servoShoulder.attach(PIN_SHOULDER); servoShoulder.write(posShoulder);
    servoElbow.attach(PIN_ELBOW);       servoElbow.write(posElbow);
    servoGripper.attach(PIN_GRIPPER);   servoGripper.write(posGripper);
    delay(300);
    Serial.println("RESUMED");

  } else if (cmd == "HOME" && !isStopped) {
    goHome();
    Serial.println("OK");
  }
}

// ============================================================
//  Setup & loop
// ============================================================
String inputBuffer = "";

void setup() {
  Serial.begin(9600);

  servoBase.attach(PIN_BASE);
  servoShoulder.attach(PIN_SHOULDER);
  servoElbow.attach(PIN_ELBOW);
  servoGripper.attach(PIN_GRIPPER);

  // Boot sequence: move to home slowly
  servoGripper.write(GRIPPER_OPEN);   delay(400);
  servoElbow.write(HOME_ELBOW);       delay(600);
  servoShoulder.write(HOME_SHOULDER); delay(600);
  servoBase.write(HOME_BASE);         delay(600);

  posBase = HOME_BASE;
  posShoulder = HOME_SHOULDER;
  posElbow = HOME_ELBOW;
  posGripper = GRIPPER_OPEN;

  Serial.println("READY");
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      processCommand(inputBuffer);
      inputBuffer = "";
    } else if (c != '\r') {
      inputBuffer += c;
    }
  }
}
