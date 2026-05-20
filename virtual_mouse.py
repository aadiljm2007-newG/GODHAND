import cv2
import mediapipe as mp
import pyautogui
import numpy as np
import time
import keyboard
import pygetwindow as gw
from pynput import keyboard as pynput_keyboard
import ctypes
import threading

# OS-Level Priority Boost (Prevents Windows from throttling the app when Desktop is focused)
try:
    kernel32 = ctypes.windll.kernel32
    # 0x00000080 is HIGH_PRIORITY_CLASS
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00000080)
    print("Process Priority Boosted to HIGH")
except:
    pass

# --- Configuration (Optimized for Low-End Systems) ---
W_CAM, H_CAM = 320, 240  # Halved: faster frame decoding on low-end CPUs
FRAME_REDUCTION = 0    # UNLOCKED: Tracking grid now fills the entire camera feed
SMOOTHENING = 5        
SENSITIVITY = 50       # Global sensitivity (0-100), 50 is neutral
PYAUTOGUI_FAILSAFE = False
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0    

# --- Ultra-Fast Win32 Mouse Engine ---
def move_mouse_fast(x, y):
    # Direct Kernel movement - Bypasses event queue
    ctypes.windll.user32.SetCursorPos(int(x), int(y))

def get_mouse_pos_fast():
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

# Global Cached Window Rect for performance
cam_win_cache = None

# --- Real-Time Camera Thread ---
class CameraThread:
    def __init__(self, src=0):
        self.cap = cv2.VideoCapture(src)
        self.cap.set(3, W_CAM)
        self.cap.set(4, H_CAM)
        self.grabbed, self.frame = self.cap.read()
        self.frame_id = 0
        self.started = False
        self.read_lock = threading.Lock()

    def start(self):
        if self.started: return None
        self.started = True
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()
        return self

    def update(self):
        while self.started:
            grabbed, frame = self.cap.read()
            with self.read_lock:
                self.grabbed = grabbed
                self.frame = frame
                self.frame_id += 1

    def read(self):
        with self.read_lock:
            frame = self.frame.copy() if self.frame is not None else None
            return self.grabbed, frame, self.frame_id

    def stop(self):
        self.started = False
        self.thread.join()

cam_stream = CameraThread(0).start()

mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=1,         # 1 hand = half the AI workload
    model_complexity=0,      # LITE MODEL: fastest inference for low-end CPUs
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
mp_draw = mp.solutions.drawing_utils

scr_w, scr_h = pyautogui.size()

# --- Window Setup (PiP Mode) ---
WIN_NAME = "Hand Gesture Pro"
cv2.namedWindow(WIN_NAME, cv2.WINDOW_NORMAL)
# Setting small display size (320x180) and moving to top right
cv2.resizeWindow(WIN_NAME, 320, 180)
cv2.moveWindow(WIN_NAME, scr_w - 330, 10) 

def lock_window_style(title, show_cam=True):
    """Specific OS-level lock for the PiP window for performance and invisibility."""
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            # Force Show (SW_SHOW = 5)
            ctypes.windll.user32.ShowWindow(hwnd, 5)
            
            # GWL_EXSTYLE = -20 | WS_EX_LAYERED = 0x80000 | WS_EX_TRANSPARENT = 0x20
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            
            if show_cam:
                # Opaque + Interactable (Remove Transparent flag)
                ctypes.windll.user32.SetWindowLongW(hwnd, -20, (ex_style | 0x80000) & ~0x20)
                # Alpha 255 = Fully Opaque
                ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, 0x2)
            else:
                # Invisible + Click-Through (Phantom Mode)
                ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style | 0x80000 | 0x20)
                # Alpha 3 = Nearly invisible but OS-stable
                ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 3, 0x2)

            # Standard Logic Locks
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
            style &= ~0x00010000 # Maximize
            style &= ~0x00020000 # Minimize
            style &= ~0x00040000 # Resizing
            style &= ~0x00080000 # Close
            ctypes.windll.user32.SetWindowLongW(hwnd, -16, style)
            
            # HWND_TOPMOST = -1
            # SWP_FRAMECHANGED = 0x0020
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0023)
            # Force refresh (RDW_INVALIDATE=0x1 | RDW_UPDATENOW=0x100)
            ctypes.windll.user32.RedrawWindow(hwnd, None, None, 0x0181)
            return True
        return False
    except:
        return False

def is_over_cam_window(x, y):
    if not show_cam_pov or cam_win_cache is None: return False
    # Use cached rect for zero-latency checks
    l, t, r, b = cam_win_cache
    margin = 5
    return (l - margin) <= x <= (r + margin) and \
           (t - margin) <= y <= (b + margin)

def on_trackbar(val):
    global SENSITIVITY
    SENSITIVITY = val

cv2.createTrackbar("Sens", WIN_NAME, SENSITIVITY, 100, on_trackbar)

# State variables
p_loc_x, p_loc_y = 0, 0
c_loc_x, c_loc_y = 0, 0
is_active = False
last_toggle_time = 0
prev_right_orientation = None 
clutch_offset_x = 0
clutch_offset_y = 0
hand_present_prev = False

# Internal smoothing variables
p_loc_y_internal = 0

# UI Hotkey States
show_cam_pov = True
prev_show_cam_pov = True
edit_overlay = True

def on_press(key):
    global show_cam_pov, edit_overlay
    try:
        # Num Lock toggle for overlay
        if key == pynput_keyboard.Key.num_lock:
            edit_overlay = not edit_overlay
            print(f"Overlay {'ENABLED' if edit_overlay else 'DISABLED'}")
        
        # '-' toggle for Camera POV
        if hasattr(key, 'char') and key.char == '-':
            show_cam_pov = not show_cam_pov
            print(f"Requesting Camera Toggle: {'SHOW' if show_cam_pov else 'HIDE'}")
    except Exception:
        pass

# Start the background hotkey listener
listener = pynput_keyboard.Listener(on_press=on_press)
listener.start()

# INITIAL LOCKING
# Briefly show then lock the window
time.sleep(0.1)
lock_window_style(WIN_NAME, show_cam=True)

def get_distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5

def is_hand_open(lm_list):
    # Check if index, middle, ring, and pinky are extended
    fingers_open = 0
    for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
        if lm_list[tip][1] < lm_list[pip][1]:
            fingers_open += 1
    return fingers_open >= 3




print("System Initialized.")
print("GESTURE GUIDE:")
print("- TOGGLE: Left Hand Open + Right Hand Flip")
print("- THUMB: Move Cursor")

# Internal filter states (Must be outside loop)
filter_x, filter_y = 0, 0
last_frame_time = time.time()
last_new_frame_id = -1
last_hand_seen_time = time.time()
results = None

while True:
    success, img, current_frame_id = cam_stream.read()
    if not success or img is None:
        time.sleep(0.1)
        continue
    
    # Calculate Loop Timing
    current_time = time.time()
    dt = current_time - last_frame_time
    last_frame_time = current_time

    # HEARTBEAT & IDLE COOLING
    # If no hands for 2 seconds, slow down the main loop to save CPU
    if current_time - last_hand_seen_time > 2.0 and not is_active:
        time.sleep(0.1) # Idle Sleep
        if int(current_time) % 10 == 0:
            print("--- System Idle (Low Power Mode) ---")

    # Only process AI if it's a NEW camera frame
    # (Cursor smoothing will still run every loop for extra smoothness)
    new_data_burst = False
    if current_frame_id != last_new_frame_id:
        img_rgb = cv2.cvtColor(cv2.flip(img, 1) if show_cam_pov else img, cv2.COLOR_BGR2RGB)
        results = hands.process(img_rgb)
        last_new_frame_id = current_frame_id
        new_data_burst = True
    
    # Performance Flip for UI
    if show_cam_pov:
        img = cv2.flip(img, 1)
    
    # --- 0. Window State Sync (Handle OpenCV UI in Main Thread) ---
    if show_cam_pov:
        # Update Window Position Cache ONCE per frame
        try:
            wins = gw.getWindowsWithTitle(WIN_NAME)
            if wins:
                w = wins[0]
                # If minimized, cache is None
                if w.isMinimized: cam_win_cache = None
                else: cam_win_cache = (w.left, w.top, w.right, w.bottom)
            else: cam_win_cache = None
        except: cam_win_cache = None
    else:
        cam_win_cache = None

    if show_cam_pov != prev_show_cam_pov:
        try:
            if not show_cam_pov:
                # Phantom Mode (Invisible but focused)
                lock_window_style(WIN_NAME, show_cam=False)
                print("Transition: PHANTOM MODE (Invisibly Focused)")
            else:
                # Visible Mode
                lock_window_style(WIN_NAME, show_cam=True)
                print("Transition: VISIBLE MODE")
        except:
            pass
        prev_show_cam_pov = show_cam_pov

    left_hand_lms = None
    right_hand_lms = None
    dist_lbl = "N/A"
    
    if results and results.multi_hand_landmarks:
        last_hand_seen_time = current_time # Reset Idle clock
        for i, hand_lms in enumerate(results.multi_hand_landmarks):
            lbl = results.multi_handedness[i].classification[0].label # 'Left' or 'Right'
            
            lm_list = []
            for id, lm in enumerate(hand_lms.landmark):
                h, w, c = img.shape
                cx, cy = int(lm.x * w), int(lm.y * h)
                lm_list.append([cx, cy])
            
            # Consistent labeling logic regardless of flipping
            # If show_cam_pov is on, image is flipped (Right becomes Left)
            # If show_cam_pov is off, image is RAW (Right is Right)
            if show_cam_pov:
                if lbl == 'Right': right_hand_lms = lm_list 
                else: left_hand_lms = lm_list
            else:
                if lbl == 'Left': right_hand_lms = lm_list 
                else: left_hand_lms = lm_list
                
            if show_cam_pov and edit_overlay:
                mp_draw.draw_landmarks(img, hand_lms, mp_hands.HAND_CONNECTIONS)

        # 1. Toggle Logic: Left Hand static Open + Right Hand Flips
        if left_hand_lms and right_hand_lms:
            # Check if Left Hand is Held Open as the "Safe Key"
            l_open = is_hand_open(left_hand_lms)
            
            if l_open:
                # Check Right Hand orientation for the flip
                # (Comparing Thumb-base to Pinky-base)
                curr_right_orient = right_hand_lms[2][0] < right_hand_lms[17][0]
                
                if prev_right_orientation is not None and curr_right_orient != prev_right_orientation:
                    curr_time = time.time()
                    if curr_time - last_toggle_time > 1.5: # 1.5s cooldown
                        is_active = not is_active
                        last_toggle_time = curr_time
                        print(f"\n>>> SYSTEM {'ACTIVATED' if is_active else 'DEACTIVATED'} <<<")
                
                prev_right_orientation = curr_right_orient
                
                if show_cam_pov and edit_overlay and not is_active:
                    cv2.putText(img, "LEFT OPEN DETECTED - FLIP RIGHT PALM TO START", (20, 100), 
                                cv2.FONT_HERSHEY_PLAIN, 1.2, (255, 255, 0), 2)
            else:
                prev_right_orientation = None
        else:
            prev_right_orientation = None
            # Debug: Only print if hands are lost during a toggle attempt
            if not is_active and (left_hand_lms or right_hand_lms):
                pass 

        # 2. Control Logic
        if is_active:
            # Dual-Hand Logic: Always use Right for Cursor, use Left for Click-Hold Toggle
            ctrl_hand = right_hand_lms
            click_hand = left_hand_lms
            
            if ctrl_hand:
                # --- 1. Distance & Scale Adaptation ---
                # reference_scale is the hand size at ~2 feet from 720p camera
                reference_scale = 120 
                current_scale = get_distance(ctrl_hand[0], ctrl_hand[9])
                scale_multiplier = current_scale / reference_scale
                
                # Distance Label
                if current_scale < 90: dist_lbl = "FAR"
                elif current_scale > 160: dist_lbl = "NEAR"
                else: dist_lbl = "IDEAL"

                # --- Precision Anchor Fusion ---
                # Instead of just the tip, we blend landmarks 4, 3, and 2 for a stabilized center.
                # Weighted blend: 50% Tip, 30% IP Joint, 20% MCP
                t4 = ctrl_hand[4]
                t3 = ctrl_hand[3]
                t2 = ctrl_hand[2]
                
                raw_x1 = (t4[0] * 0.5) + (t3[0] * 0.3) + (t2[0] * 0.2)
                raw_y1 = (t4[1] * 0.5) + (t3[1] * 0.3) + (t2[1] * 0.2)
                
                # Restore tips for UI feedback
                index_tip = ctrl_hand[8]
                middle_tip = ctrl_hand[12]
                
                x1, y1 = raw_x1, raw_y1

                # --- 2. Dynamic Rig & Smoothing ---
                # Far away -> Hand is small -> We need a SMALLER box (higher reduction) to reach corners
                # Close up -> Hand is big -> We need a BIGGER box (lower reduction) for precision
                # ULTIMATE EXPANSION: 0 reduction means the tracking square is the size of the camera feed
                # At Sens 50, we now have ZERO reduction (Maximum Grid)
                base_reduction = int(np.interp(SENSITIVITY, [0, 50, 100], [0, 0, 100]))
                
                # Minimizing distance compensation to keep the grid large
                # Far hand (60) now only adds 40px reduction for massive reach
                distance_comp = int(np.interp(current_scale, [60, 120, 250], [40, 0, -40]))
                dynamic_reduction = max(0, base_reduction + distance_comp)
                
                # Smoothing: Higher floor to prevent jaggedness
                # Base smoothing ranges from 4 (fast) to 12 (stable)
                smooth_base = max(4, 12 - (SENSITIVITY // 10))
                distance_smooth = int(np.interp(current_scale, [60, 150], [6, 0]))
                dynamic_smooth = max(4, smooth_base + distance_smooth)


                # --- 2. Ultra-Smooth Cursor Mapping ---
                if show_cam_pov and edit_overlay:
                    cv2.rectangle(img, (dynamic_reduction, dynamic_reduction), 
                                  (W_CAM - dynamic_reduction, H_CAM - dynamic_reduction), (255, 0, 255), 2)

                # Adaptive Mapping: X-axis must be inverted if we aren't flipping the image (Background Mode)
                if show_cam_pov:
                    x_raw = np.interp(x1, (dynamic_reduction, W_CAM - dynamic_reduction), (0, scr_w))
                else:
                    x_raw = np.interp(x1, (dynamic_reduction, W_CAM - dynamic_reduction), (scr_w, 0))
                
                y_raw = np.interp(y1, (dynamic_reduction, H_CAM - dynamic_reduction), (0, scr_h))
                
                # --- 3. Anti-Jump Clutching ---
                if not hand_present_prev:
                    # Hand just appeared! Sync the rig to current mouse position
                    curr_mx, curr_my = get_mouse_pos_fast()
                    clutch_offset_x = curr_mx - x_raw
                    clutch_offset_y = curr_my - y_raw
                    # Reset smoothing to prevent 'glide-in' from old position
                    p_loc_x, p_loc_y = curr_mx, curr_my
                
                hand_present_prev = True
                
                # Apply the clutch offset
                x_mapped = x_raw + clutch_offset_x
                y_mapped = y_raw + clutch_offset_y

                # Jitter Deadzone (Lowered to 2px for better precision with SetCursorPos)
                if abs(x_mapped - p_loc_x) < 2: x_mapped = p_loc_x
                if abs(y_mapped - p_loc_y) < 2: y_mapped = p_loc_y

                # EMA Smoothing (Adaptive Velocity-Sensitive Smoothing)
                # Calculate movement distance to adjust responsiveness
                dist_moved = ((x_mapped - p_loc_x)**2 + (y_mapped - p_loc_y)**2)**0.5
                
                # If moving slowly (precision work), use lower frequency for high smoothness
                # If moving fast (traveling), use higher frequency for snappiness
                target_freq = np.interp(dist_moved, [2, 60], [5.0, 18.0])
                
                # HYSTERESIS: Extra stable for micro-movements
                if dist_moved < 1.5:
                    target_freq *= 0.7 # Extra dampening for sub-pixel precision
                
                lerp_factor = 1.0 - np.exp(-target_freq * dt)
                
                c_loc_x = p_loc_x + (x_mapped - p_loc_x) * lerp_factor
                c_loc_y = p_loc_y + (y_mapped - p_loc_y) * lerp_factor

                # NORMAL MODE: Cursor Movement
                move_mouse_fast(c_loc_x, c_loc_y)



                if show_cam_pov and edit_overlay:
                    cv2.circle(img, (int(x1), int(y1)), 15, (0, 255, 0), cv2.FILLED)
                p_loc_x, p_loc_y = c_loc_x, c_loc_y
            else:
                # Active but Right Hand (Controller) not found
                hand_present_prev = False
        else:
            # System not active
            hand_present_prev = False
    else:
        # No hands detected at all
        hand_present_prev = False

    # UI Feedback
    if show_cam_pov and edit_overlay:
        status_text = "ACTIVE" if is_active else "LOCKED (Left Open + Flip Right)"
        cv2.putText(img, status_text, (20, 50), cv2.FONT_HERSHEY_PLAIN, 2, (0, 255, 0) if is_active else (0, 0, 255), 2)
        cv2.putText(img, f"Sens: {SENSITIVITY} | Dist: {dist_lbl}", (20, 85), cv2.FONT_HERSHEY_PLAIN, 1.2, (255, 255, 255), 2)
    
    if show_cam_pov:
        cv2.imshow(WIN_NAME, img)
        if cv2.waitKey(1) & 0xFF == 27: break
    else:
        # PURE PHANTOM MODE: Maintaining the window object for OS priority
        # Keeping a static frame to avoid flickering while invisible
        phantom_img = np.zeros((180, 320, 3), dtype=np.uint8)
        cv2.imshow(WIN_NAME, phantom_img)
        cv2.waitKey(1)

cam_stream.cap.release()
cv2.destroyAllWindows()
