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
W_CAM, H_CAM = 640, 480 
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
    max_num_hands=2, 
    model_complexity=0,      # LITE MODEL: Fastest for low-end CPUs
    min_detection_confidence=0.8, # Increased for higher precision
    min_tracking_confidence=0.8
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
scroll_mode = False
last_toggle_time = 0
last_scroll_toggle_time = 0
prev_right_orientation = None 
glued_gesture_held = False 
scroll_midpoint_y = 0
was_grabbing = False
# Click & Movement states
is_holding_left = False
pinch_start_time = 0
pinch_start_pos = (0, 0)
click_lock_pos = (0, 0) # Snapshot for tap stability
last_release_time = 0
clutch_offset_x = 0
clutch_offset_y = 0
hand_present_prev = False
last_l_click_time = 0 # Debouncer
last_r_click_time = 0 # Debouncer

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


def is_fist(lm_list):
    # Check if index, middle, ring, and pinky are extended
    # In a fist, the tips should be BELOW their respective PIP joints (landmark y > pip y)
    fingers_closed = 0
    for tip, pip in [(8, 6), (12, 10), (16, 14), (20, 18)]:
        # In frame coordinates, higher index is lower on screen
        if lm_list[tip][1] > lm_list[pip][1]:
            fingers_closed += 1
    
    # Thumb check: tip usually tucked in
    thumb_tucked = lm_list[4][0] > lm_list[3][0] # Simple left/right tuck check for right hand
    
    return fingers_closed == 4

print("System Initialized.")
print("GESTURE GUIDE:")
print("- TOGGLE: Left Hand Open + Right Hand Flip")
print("- THUMB: Move Cursor")
print("- THUMB + INDEX + MIDDLE PINCH (Left): Left Click / Drag")
print("- MIDDLE + RING PINCH (Left Flip): Right Click")
print("- MAKE FIST (Right): TOGGLE Scroll Mode")
print("- LEFT PINCH + RIGHT MOVE (In Scroll): Scroll Joystick")

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
                if is_holding_left: dynamic_smooth *= 1.5

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
                if is_holding_left: lerp_factor *= 0.5 
                
                c_loc_x = p_loc_x + (x_mapped - p_loc_x) * lerp_factor
                c_loc_y = p_loc_y + (y_mapped - p_loc_y) * lerp_factor

                # --- 1. Scroll Toggle Logic (Right Hand Fist) ---
                curr_glued = is_fist(ctrl_hand)
                curr_time = time.time()
                
                if curr_glued and not glued_gesture_held:
                    # Increased cooldown to 1.5s for better stability
                    if curr_time - last_scroll_toggle_time > 1.5:
                        scroll_mode = not scroll_mode
                        last_scroll_toggle_time = curr_time
                        if scroll_mode: pass # Reset on entry
                        print(f"SCROLL MODE {'ENABLED' if scroll_mode else 'DISABLED'}")
                glued_gesture_held = curr_glued

                # --- 2. Action Logic ---
                if scroll_mode:
                    if show_cam_pov and edit_overlay:
                        # Visual Feedback: WHOLE HAND Green Overlay
                        overlay = img.copy()
                        points = np.array(ctrl_hand)
                        hull = cv2.convexHull(points)
                        cv2.fillPoly(overlay, [hull], (0, 255, 0))
                        cv2.addWeighted(overlay, 0.4, img, 0.6, 0, img)
                        cv2.putText(img, "LEFT GRAB + RIGHT MOVE", (W_CAM//2 - 200, 50), cv2.FONT_HERSHEY_PLAIN, 2, (0, 255, 0), 2)
                    
                    # Joystick Constants
                    deadzone = 35
                    
                    # --- Left Hand Grabbing ---
                    dist_l_click = 999
                    if click_hand:
                        l_thumb, l_index = click_hand[4], click_hand[8]
                        l_hand_scale = get_distance(click_hand[0], click_hand[9])
                        l_threshold = max(25, l_hand_scale * 0.3)
                        dist_l_click = get_distance(l_thumb, l_index)
                        is_grabbing = dist_l_click < l_threshold
                    else:
                        is_grabbing = False
                    
                    if is_grabbing:
                        # SET MIDPOINT ON START OF GRAB (Using Right hand y for joystick)
                        if not was_grabbing:
                            scroll_midpoint_y = y1
                        
                        if show_cam_pov and edit_overlay:
                            # Draw Dynamic Deadzone Visuals around midpoint
                            cv2.line(img, (0, int(scroll_midpoint_y - deadzone)), (W_CAM, int(scroll_midpoint_y - deadzone)), (0, 0, 150), 2)
                            cv2.line(img, (0, int(scroll_midpoint_y + deadzone)), (W_CAM, int(scroll_midpoint_y + deadzone)), (0, 0, 150), 2)
                            cv2.circle(img, (int(x1), int(scroll_midpoint_y)), 10, (0, 0, 255), 2) # Midpoint indicator
                            cv2.circle(img, (index_tip[0], index_tip[1]), 15, (0, 0, 255), cv2.FILLED)
                            cv2.putText(img, "SCROLLING", (index_tip[0]+20, index_tip[1]), cv2.FONT_HERSHEY_PLAIN, 1.5, (0, 0, 255), 2)
                        
                        # Joystick Logic: Proportional to distance from RELATIVE midpoint
                        intensity = (SENSITIVITY / 50) ** 2
                        
                        # Smooth the input Y to prevent staccato scrolling
                        y1_smooth = p_loc_y_internal + (y1 - p_loc_y_internal) / 3
                        
                        if y1_smooth < scroll_midpoint_y - deadzone:
                            dist = (scroll_midpoint_y - deadzone) - y1_smooth
                            base_speed = int(np.interp(dist, (0, 300), (5, 80)))
                            speed = int(base_speed * intensity)
                            if not is_over_cam_window(c_loc_x, c_loc_y):
                                pyautogui.scroll(speed)
                        elif y1_smooth > scroll_midpoint_y + deadzone:
                            dist = y1_smooth - (scroll_midpoint_y + deadzone)
                            base_speed = int(np.interp(dist, (0, 300), (5, 80)))
                            speed = int(base_speed * intensity)
                            if not is_over_cam_window(c_loc_x, c_loc_y):
                                pyautogui.scroll(-speed)
                        
                        p_loc_y_internal = y1_smooth
                    
                    was_grabbing = is_grabbing
                    
                    if show_cam_pov and edit_overlay:
                        cv2.circle(img, (int(x1), int(y1)), 20, (0, 255, 255), 2)
                else:
                    # NORMAL MODE: Cursor Movement + Clicks
                    move_mouse_fast(c_loc_x, c_loc_y)
                    
                    # SAFETY: If cursor enters the protected window zone, force release any clicks
                    if is_over_cam_window(c_loc_x, c_loc_y):
                        if is_holding_left:
                            pyautogui.mouseUp(button='left')
                            is_holding_left = False
                            print("Passthrough Protected: Click Force-Released")
                    
                    # --- Left Click Logic (Exclusively on Left Hand) ---
                    left_hand_hold = False
                    if click_hand:
                        l_thumb = click_hand[4]
                        l_index = click_hand[8] 
                        l_middle = click_hand[12]
                        
                        l_hand_scale = get_distance(click_hand[0], click_hand[9])
                        
                        # 1. Check if Index and Middle are CLOSE (joined)
                        join_threshold = max(25, l_hand_scale * 0.4) 
                        fingers_joined = get_distance(l_index, l_middle) < join_threshold
                        
                        # 2. Check if Thumb makes contact with the Index (which is joined to Middle)
                        # We use a slightly looser threshold because 3 fingers are harder to perfect-pinch
                        click_threshold = max(22, l_hand_scale * 0.28) 
                        thumb_touching = get_distance(l_thumb, l_index) < click_threshold
                        
                        if fingers_joined and thumb_touching:
                            left_hand_hold = True
                            
                        if show_cam_pov and edit_overlay and left_hand_hold:
                                cv2.circle(img, (l_index[0], l_index[1]), 15, (0, 0, 255), cv2.FILLED)
                                cv2.circle(img, (l_middle[0], l_middle[1]), 15, (0, 0, 255), cv2.FILLED)
                                cv2.putText(img, "3-FINGER CLICK", (l_index[0]+20, l_index[1]), cv2.FONT_HERSHEY_PLAIN, 1.2, (0, 0, 255), 2)
                    
                    # Right hand NO LONGER performs its own pinch-clicks
                    trigger_hold = left_hand_hold
                    
                    if trigger_hold:
                        curr_time = time.time()
                        # CLICK DEBOUNCER
                        if not is_holding_left and (curr_time - last_l_click_time > 0.15):
                            if not is_over_cam_window(c_loc_x, c_loc_y):
                                # SNAPSHOT: Lock the position for a perfect tap
                                click_lock_pos = (c_loc_x, c_loc_y)
                                pyautogui.mouseDown(button='left')
                                is_holding_left = True
                                pinch_start_time = curr_time
                                pinch_start_pos = (x1, y1)
                                label = "FIXATED"
                            else:
                                label = "ZONE PROTECTED"
                        
                        # --- 100% ACCURACY LOCK ---
                        # For the first 120ms, if the hand hasn't moved much, FORCE the cursor to stay at the start pos.
                        # This avoids the "slippery" click feel.
                        if is_holding_left:
                            lock_duration = curr_time - pinch_start_time
                            dist_from_start = get_distance(pinch_start_pos, (x1, y1))
                            
                            # Break lock if hand moves > 30px (User clearly wants to drag)
                            if lock_duration < 0.12 and dist_from_start < (30 * scale_multiplier):
                                c_loc_x, c_loc_y = click_lock_pos
                                label = "CLICK LOCK"
                                # Force SetCursor (Overriding movement loop for this specific frame)
                                move_mouse_fast(c_loc_x, c_loc_y)
                            else:
                                label = "DRAGGING"
                        
                        if show_cam_pov and edit_overlay:
                            if click_hand:
                                cv2.line(img, (click_hand[8][0], click_hand[8][1]), (int(x1), int(y1)), (0, 255, 0), 2)
                            cv2.circle(img, (index_tip[0], index_tip[1]), 15, (0, 255, 255) if label == "CLICK LOCK" else (0, 0, 255), cv2.FILLED)
                            cv2.putText(img, label, (index_tip[0]+20, index_tip[1]), cv2.FONT_HERSHEY_PLAIN, 1.2, (255, 255, 0), 2)
                    else:
                        if is_holding_left:
                            # STABILIZED TIMING: Enforce a 50ms hold so Windows registers it
                            if time.time() - pinch_start_time < 0.05:
                                time.sleep(0.05)
                            pyautogui.mouseUp(button='left')
                            is_holding_left = False
                            last_l_click_time = time.time()
                    
                    # Specialized Right Click: Applicable ONLY for Left Hand per split-hand rules
                    lh_flipped = left_hand_lms[4][0] < left_hand_lms[20][0] if left_hand_lms else False
                    
                    if lh_flipped:
                        lt_tip = left_hand_lms[12] # Middle
                        lr_tip = left_hand_lms[16] # Ring
                        dist_lh = get_distance(lt_tip, lr_tip)
                        # Ensure Index finger is OPEN to distinguish from fist or other gestures
                        is_index_open = left_hand_lms[8][1] < left_hand_lms[6][1] 
                        if dist_lh < (20 * scale_multiplier) and is_index_open:
                            curr_time = time.time()
                            # RIGHT CLICK DEBOUNCER
                            if (curr_time - last_r_click_time > 0.3):
                                if not is_over_cam_window(c_loc_x, c_loc_y):
                                    # STABILIZE: Freeze cursor for Right Click context menus
                                    move_mouse_fast(c_loc_x, c_loc_y)
                                    pyautogui.mouseDown(button='right')
                                    time.sleep(0.08)
                                    pyautogui.mouseUp(button='right')
                                    last_r_click_time = time.time()
                                    if show_cam_pov and edit_overlay:
                                        cv2.circle(img, (lt_tip[0], lt_tip[1]), 20, (255, 0, 0), cv2.FILLED)
                                        cv2.putText(img, "RIGHT CLICK LOCK", (lt_tip[0]+20, lt_tip[1]), cv2.FONT_HERSHEY_PLAIN, 1.2, (255, 0, 0), 2)
                            else:
                                if show_cam_pov and edit_overlay: cv2.putText(img, "PROTECTED", (lt_tip[0], lt_tip[1]), cv2.FONT_HERSHEY_PLAIN, 1.2, (0, 0, 255), 2)
                        elif show_cam_pov and edit_overlay:
                            cv2.circle(img, (lt_tip[0], lt_tip[1]), 10, (255, 255, 0), 2)
                            cv2.circle(img, (lr_tip[0], lr_tip[1]), 10, (255, 255, 0), 2)


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
