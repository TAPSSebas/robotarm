// ============================================================
//  EEZYbotARM MK3 — Arduino Sketch
//  Serial commands van Raspberry Pi @ 9600 baud
//
//  Servo wiring:
//    Base     → pin 9
//    Shoulder → pin 10
//    Elbow    → pin 11
//    Gripper  → pin 6
//
//  Nieuwe PICK syntax:
//    PICK px py drop_base drop_shoulder drop_elbow
//  Drop-posities komen nu vanuit app.py (config.txt),
//  dus de Arduino hoeft ze niet meer hardcoded te hebben.
// ============================================================

#include <Servo.h>

Servo servoBase;
Servo servoShoulder;
Servo servoElbow;
Servo servoGripper;

#define PIN_BASE      9
#define PIN_SHOULDER  10
#define PIN_ELBOW     11
#define PIN_GRIPPER   6

// Servo limieten (graden)
#define BASE_MIN       10
#define BASE_MAX       170
#define SHOULDER_MIN   40
#define SHOULDER_MAX   140
#define ELBOW_MIN      30
#define ELBOW_MAX      150
#define GRIPPER_OPEN   30
#define GRIPPER_CLOSED 100

// Home positie
#define HOME_BASE      90
#define HOME_SHOULDER  90
#define HOME_ELBOW     90

int posBase     = HOME_BASE;
int posShoulder = HOME_SHOULDER;
int posElbow    = HOME_ELBOW;
int posGripper  = GRIPPER_OPEN;
int moveSpeed   = 50;
bool isStopped  = false;

// ============================================================
//  Smooth servo beweging
// ============================================================
void smoothMove(Servo& s, int& current, int target, int minV, int maxV) {
  target = constrain(target, minV, maxV);
  int step    = (target >= current) ? 1 : -1;
  int delayMs = map(moveSpeed, 1, 100, 18, 2);
  while (current != target) {
    current += step;
    s.write(current);
    delay(delayMs);
  }
}

void moveBase(int deg)     { smoothMove(servoBase,     posBase,     deg, BASE_MIN,     BASE_MAX);     }
void moveShoulder(int deg) { smoothMove(servoShoulder, posShoulder, deg, SHOULDER_MIN, SHOULDER_MAX); }
void moveElbow(int deg)    { smoothMove(servoElbow,    posElbow,    deg, ELBOW_MIN,    ELBOW_MAX);     }
void moveGripper(int deg)  {
  posGripper = constrain(deg, GRIPPER_OPEN, GRIPPER_CLOSED);
  servoGripper.write(posGripper);
  delay(400);
}

void goHome() {
  moveShoulder(HOME_SHOULDER);
  moveElbow(HOME_ELBOW);
  moveBase(HOME_BASE);
}

// ============================================================
//  PICK  px py drop_base drop_shoulder drop_elbow
//
//  - px/py  : pixel coördinaten van het object (640×480)
//  - drop_* : bakje-positie, bepaald door config.txt via Pi
// ============================================================
void doPick(int px, int py, int dropBase, int dropShoulder, int dropElbow) {
  // Pixel → arm hoeken
  int targetBase     = map(px, 0, 640, 160, 20);
  int targetShoulder = map(py, 0, 480, 120,  55);
  int targetElbow    = map(py, 0, 480,  50, 110);

  targetBase     = constrain(targetBase,     BASE_MIN,     BASE_MAX);
  targetShoulder = constrain(targetShoulder, SHOULDER_MIN, SHOULDER_MAX);
  targetElbow    = constrain(targetElbow,    ELBOW_MIN,    ELBOW_MAX);

  // 1. Gripper open
  moveGripper(GRIPPER_OPEN);

  // 2. Arm omhoog naar home, dan naar object draaien
  moveShoulder(HOME_SHOULDER);
  moveElbow(HOME_ELBOW);
  moveBase(targetBase);

  // 3. Aanvliegen (iets boven object)
  moveShoulder(targetShoulder + 15);
  moveElbow(targetElbow);

  // 4. Zakken op object
  moveShoulder(targetShoulder);
  delay(150);

  // 5. Grijpen
  moveGripper(GRIPPER_CLOSED);

  // 6. Optillen
  moveShoulder(targetShoulder + 20);

  // 7. Draaien naar bakje
  moveBase(dropBase);
  moveShoulder(dropShoulder);
  moveElbow(dropElbow);
  delay(200);

  // 8. Loslaten
  moveGripper(GRIPPER_OPEN);
  delay(200);

  // 9. Terug home
  goHome();

  Serial.println("DONE");
}

// ============================================================
//  JOG
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
//  Command parser
// ============================================================
void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd.startsWith("PICK") && !isStopped) {
    int px, py, db, ds, de;
    // Nieuwe syntax: PICK px py drop_base drop_shoulder drop_elbow
    if (sscanf(cmd.c_str(), "PICK %d %d %d %d %d", &px, &py, &db, &ds, &de) == 5) {
      doPick(px, py, db, ds, de);
    } else {
      // Backwards compat: PICK px py (gebruik fallback drop)
      if (sscanf(cmd.c_str(), "PICK %d %d", &px, &py) == 2) {
        doPick(px, py, 170, 80, 100);
      }
    }

  } else if (cmd.startsWith("JOG") && !isStopped) {
    char dir[16]; int spd = moveSpeed;
    sscanf(cmd.c_str(), "JOG %15s %d", dir, &spd);
    doJog(dir, spd);

  } else if (cmd.startsWith("GRIPPER") && !isStopped) {
    char action[10];
    sscanf(cmd.c_str(), "GRIPPER %9s", action);
    if (String(action) == "open") moveGripper(GRIPPER_OPEN);
    else                          moveGripper(GRIPPER_CLOSED);
    Serial.println("OK");

  } else if (cmd.startsWith("SPEED")) {
    int s;
    if (sscanf(cmd.c_str(), "SPEED %d", &s) == 1)
      moveSpeed = constrain(s, 1, 100);
    Serial.println("OK");

  } else if (cmd == "STOP") {
    isStopped = true;
    servoBase.detach();
    servoShoulder.detach();
    servoElbow.detach();
    servoGripper.detach();
    Serial.println("STOPPED");

  } else if (cmd == "RESUME") {
    isStopped = false;
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