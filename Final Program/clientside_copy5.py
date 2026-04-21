from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QGroupBox, QPushButton, QLineEdit, QCheckBox, QDoubleSpinBox, QTableWidgetItem
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QThread
from PyQt5.QtGui import QImage, QPixmap
from PyQt5 import uic
import sys
import socket
import cv2
import os
import numpy as np
from datetime import datetime
import time
import logging
from acqwindow_copy5 import AcqWindow
import ast
import threading

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("client.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class SocketClass:
    def __init__(self, host, port, socket_type=socket.SOCK_STREAM, socket_name = "", timeout=5.0):
        self.host = host
        self.port = port
        self.socket_type = socket_type
        self.socket_name = socket_name
        self.timeout = timeout
        self.socket = None
        self.is_connected = False
    
    def connect(self):
        """Establish connection to the server"""
        try:
            self.socket = socket.socket(socket.AF_INET, self.socket_type)
            self.socket.settimeout(self.timeout)
            
            if self.socket_type == socket.SOCK_STREAM:  # TCP
                self.socket.connect((self.host, self.port))
                self.is_connected = True
                return True, f"{self.socket_name} SOCKET connected"
            else:  # UDP
                self.socket.bind(('0.0.0.0', self.port))
                self.is_connected = True
                return True, f"{self.socket_name} SOCKET connected"
                
        except Exception as e:
            logger.error(f"{self.socket_name} SOCKET connection error: {str(e)}")
            return False, f"{self.socket_name} SOCKET connection error: {str(e)}"
    
    def disconnect(self):
        """Close the socket connection"""
        if self.socket:
            try:
                self.socket.close()
                self.is_connected = False
                return True, "Connection closed"
            except Exception as e:
                logger.error(f"Disconnect error: {str(e)}")
                return False, f"Disconnect error: {str(e)}"
        return True, "No active connection to close"
    
    def send(self, data):
        """Send data through socket"""
        if not self.is_connected or not self.socket:
            return False, "Not connected"
        
        try:
            if isinstance(data, str):
                data = data.encode('utf-8')
            
            if self.socket_type == socket.SOCK_STREAM:  # TCP
                self.socket.sendall(data)
            else:  # UDP
                self.socket.sendto(data, (self.host, self.port))
                
            return True, "Data sent successfully"
        except socket.timeout:
            logger.error("Send timeout")
            return False, "Send timeout"
        except Exception as e:
            logger.error(f"Send error: {str(e)}")
            return False, f"Send error: {str(e)}"
    
    def receive(self, buffer_size=4096, expect_response=True):
        """Receive data from socket"""
        if not self.is_connected or not self.socket:
            return False, "Not connected", None
        
        try:
            if self.socket_type == socket.SOCK_STREAM:  # TCP
                data = self.socket.recv(buffer_size)
            else:  # UDP
                data, _ = self.socket.recvfrom(buffer_size)
                
            if not data and expect_response:
                logger.warning("No data received")
                return False, "No data received", None
                
            return True, "Data received", data
        except socket.timeout:
            logger.error("Receive timeout")
            return False, "Receive timeout", None
        except Exception as e:
            logger.error(f"Receive error: {str(e)}")
            return False, f"Receive error: {str(e)}", None

class VideoThread(QThread):
    frame_signal = pyqtSignal(np.ndarray)
    status_signal = pyqtSignal(bool, str) # (success, response)

    def __init__(self, host, video_port):
        super().__init__()
        self.video_socket = SocketClass(host, video_port, socket.SOCK_DGRAM, "VIDEO")
        self.is_videorunning = False

    def run(self):
        # Connect video socket ke server
        success, response = self.video_socket.connect()
        if success == False:
            logger.error(response)
            self.status_signal.emit(False, response)
            return

        self.is_videorunning = True
        logger.info(response)
        self.status_signal.emit(True, response)

        while self.is_videorunning:
            try:
                success, message, data = self.video_socket.receive(65536)
                if success and data:
                    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        self.frame_signal.emit(frame)
            except Exception as e:
                logger.error(f"Video frame processing error: {str(e)}")
                self.status_signal.emit(False, f"Video frame processing error: {str(e)}")  
        
        self.video_socket.disconnect()      
    
    def stop(self):
        self.is_videorunning = False
        self.wait()

class ImageThread(QThread):
    status_signal = pyqtSignal(bool, str) # (success, response)

    def __init__(self, host, image_port, capture_dir, filename):
        super().__init__()
        self.image_socket = SocketClass(host, image_port, socket_name="IMAGE", timeout=10.0)
        self.capture_dir = capture_dir
        self.filename = filename

    def run(self):
        success, response = self.image_socket.connect()
        if success == False:
            logger.error(response)
            self.status_signal.emit(False, response)
            return
        
        logger.info(response)
        self.status_signal.emit(True, response)
        
        try:
            # send CAPTURE command
            success, response = self.image_socket.send("CAPTURE")
            if not success:
                self.status_signal.emit(False, f"Send error: {response}")
                self.image_socket.disconnect()
                return
            
            # receive Image Size data
            success, response, size_data = self.image_socket.receive(8)
            if not success:
                self.status_signal.emit(False, f"Size receive error: {response}")
                self.image_socket.disconnect()
                return
        
            # check error message
            if size_data.startswith(b"ERROR - "):
                error_msg = self.image_socket.receive(1024)[2].decode('utf-8')
                self.status_signal.emit(False, f"Server error: {error_msg.strip()}")
                self.image_socket.disconnect()
                return
            size = int.from_bytes(size_data, byteorder='big')

            # receive Image data
            received_data = b''
            while len(received_data) < size:
                chunk_size = min(4096, size - len(received_data))
                success, response, chunk = self.image_socket.receive(chunk_size)
                if not success or not chunk:
                    break
                received_data += chunk

            # save Image to file
            save_path = os.path.join(self.capture_dir, self.filename)
            with open(save_path, 'wb') as f:
                f.write(received_data)
                
            self.status_signal.emit(True, f"Image saved to {save_path}")

        except Exception as e:
            logger.error(f"Capture error: {str(e)}")
            self.status_signal.emit(False, f"Capture error: {str(e)}")
        finally:
            self.image_socket.disconnect()

class PositionThread(QThread):
    position_signal = pyqtSignal(str, dict)  # ("IDLE", {'X':0, 'Y':0, 'Z':0})
    status_signal = pyqtSignal(bool, str) # (success, response)
    
    def __init__(self, host, position_port):
        super().__init__()
        self.position_socket = SocketClass(host, position_port, socket_name="POSITION")

    def run(self):
        success, response = self.position_socket.connect()
        if success == False:
            logger.error(response)
            self.status_signal.emit(False, response)
            return

        self.is_positionrunning = True
        logger.info(response)
        self.status_signal.emit(True, response)

        while self.is_positionrunning:
            try:
                success, response, data = self.position_socket.receive()
                if success and data:
                    status_data, position_data = self.parse_position_data(data.decode('utf-8'))
                    self.position_signal.emit(status_data, position_data)
            except Exception as e:
                pass
                # logger.error(f"Position monitoring error: {str(e)}")
                # self.status_signal.emit(False, f"Position monitoring error: {str(e)}")

        self.position_socket.disconnect()

    def parse_position_data(self, data):
        # Parse position data from server
        result = ast.literal_eval(data)
        status, position = result
        return status, position

    def stop(self):
        self.is_positionrunning = False
        self.wait()

class Client(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi('mainlayout.ui', self)

        #========== BUTTON CONNECTIONS ==========#
        self.startvideoButton.clicked.connect(self.toggle_videostream)
        self.captureButton.clicked.connect(lambda: self.capture_image())
        self.xplusButton.clicked.connect(lambda: self.jog_move('X', 1))
        self.xminButton.clicked.connect(lambda: self.jog_move('X', -1))
        self.yplusButton.clicked.connect(lambda: self.jog_move('Y', 1))
        self.yminButton.clicked.connect(lambda: self.jog_move('Y', -1))
        self.zplusButton.clicked.connect(lambda: self.jog_move('Z', 1))
        self.zminButton.clicked.connect(lambda: self.jog_move('Z', -1))
        self.acquisitionButton.clicked.connect(self.open_acquisition_window)
        self.zoomSlider.valueChanged.connect(self.update_zoomLabel)

        #========== SYSTEM SETTINGS ==========#
        self.SERVER_NAME = 'kursiplastik.local'
        
        try:
            self.SERVER_IP = socket.gethostbyname('kursiplastik')
        except:
            logger.warning(f"Could not resolve server name: {self.SERVER_NAME}")
            self.SERVER_IP = '127.0.0.1'
        
        self.HOST = '0.0.0.0'
        self.CONTROL_PORT = 7070
        self.VIDEO_PORT = 7071
        self.IMAGE_PORT = 7072
        self.POSITION_PORT = 7073
        self.CURRENT_POSITION = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
        self.CURRENT_STATUS = 'Idle'
        self.MOVE_BOUNDARY = {
            'X': {'min': -1000.0, 'max': 1000.0},
            'Y': {'min': -1000.0, 'max': 1000.0},
            'Z': {'min': -250.0, 'max': 250.0}
        }
        
        # self.capture_done_flag = False
        # self.capture_done_lock = threading.Lock()
        self.current_status_lock = threading.Lock()

        #========== INITIALIZATION ==========#
        self.acq_window = None
        self.video_thread = None
        self.position_thread = None
        self.persistent_image_socket = None # For acquisition window

        self.is_videorunning = False
        self.was_videorunning = False

        self.capture_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Captures")
        os.makedirs(self.capture_dir, exist_ok=True)

        #========== CONTROL SOCKET ==========#
        # Connect control socket ke server
        self.control_socket = SocketClass(self.SERVER_IP, self.CONTROL_PORT, socket_name="CONTROL")
        success, response = self.control_socket.connect()
        if not success:
            logger.error(response)
            self.handle_status_signal(False, response)
        elif success:
            logger.info(response)
            self.handle_status_signal(True, response)

        #========== POSITION SOCKET ==========#
        self.position_thread = PositionThread(self.SERVER_IP, self.POSITION_PORT)
        self.position_thread.position_signal.connect(self.update_position)
        self.position_thread.status_signal.connect(self.handle_status_signal)
        self.position_thread.start()

    def update_zoomLabel(self, value):
        self.zoomLabel.setText(f"Zoom Level: {value/10:.1f}x")
    
    #========== CONTROL ==========#
    def send_command(self, command, buffer_size=4096):
        success, response = self.control_socket.send(command)
        if not success:
            return False, response
        
        success, response, data = self.control_socket.receive(buffer_size)
        if not success:
            return False, response
        
        if isinstance(data, bytes):
            try:
                response = data.decode('utf-8')
            except UnicodeDecodeError:
                pass
        return True, f"{command} executed: {response}"
    
    #========== VIDEO STREAM ==========#
    def toggle_videostream(self):
        if not self.is_videorunning:
            # Start video stream
            success, response = self.send_command("START_VIDEO")
            if "started" in response.lower() or success:
                self.video_thread = VideoThread(self.SERVER_IP, self.VIDEO_PORT)
                self.video_thread.frame_signal.connect(self.update_videoframe)
                self.video_thread.status_signal.connect(self.handle_status_signal)
                self.video_thread.start()
                self.is_videorunning = True
                self.startvideoButton.setText("Stop Video Stream")
                self.log_activity(f"INFO - {response}")
            else:
                self.log_activity(f"ERROR - {response}")
        else:
            # Stop video stream
            if self.video_thread:
                success, response = self.send_command("STOP_VIDEO")
                self.video_thread.stop()
                self.video_thread.wait()
                self.video_thread = None
                self.is_videorunning = False
                self.startvideoButton.setText("Start Video Stream")
                # Use server's response or a generic client message
                if "stopped" in response.lower() or success:
                     self.log_activity(f"INFO - {response}")
                else:
                     self.log_activity(f"INFO - Video stream stop requested. Client stream stopped. Server: {response}")
            else: # Should not happen if is_videorunning is true
                self.is_videorunning = False 
                self.startvideoButton.setText("Start Video Stream")
                self.log_activity("WARNING - Video stream was marked running but thread was missing.")
    
    # def update_videoframe(self, frame):
    #     image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    #     h, w, ch = image.shape
    #     bytes_per_line = ch*w
    #     qt_image = QImage(image.data, w, h, bytes_per_line, QImage.Format_RGB888)
    #     self. videoLabel.setPixmap(QPixmap.fromImage(qt_image).scaled(
    #             self.videoLabel.width(),
    #             self.videoLabel.height(),
    #             Qt.KeepAspectRatio))

    def update_videoframe(self, frame):
        # 1. Convert BGR ke RGB
        image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # 2. Crop untuk zoom digital
        # zoom_factor = self.zoomSlider.value()/10
        # print(zoom_factor)
        # h, w, ch = image.shape
        # if zoom_factor > 1:
        #     new_w = w // zoom_factor
        #     new_h = h // zoom_factor
        #     cx, cy = w // 2, h // 2
        #     image = image[cy - new_h//2:cy + new_h//2, cx - new_w//2:cx + new_w//2]
        #     # 3. Resize kembali ke ukuran asli
        #     image = cv2.resize(image, (w, h), interpolation=cv2.INTER_LINEAR)

        zoom_factor = self.zoomSlider.value() / 10

        h, w, ch = image.shape
        if zoom_factor > 1.0:
            new_w = int(w / zoom_factor)
            new_h = int(h / zoom_factor)
            cx, cy = w // 2, h // 2

            image = image[
                max(0, cy - new_h // 2):min(h, cy + new_h // 2),
                max(0, cx - new_w // 2):min(w, cx + new_w // 2)
            ]

            image = cv2.resize(image, (w, h), interpolation=cv2.INTER_LINEAR)

        # 4. Convert ke QImage dan tampilkan
        bytes_per_line = ch * w
        qt_image = QImage(image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        self.videoLabel.setPixmap(QPixmap.fromImage(qt_image).scaled(
            self.videoLabel.width(),
            self.videoLabel.height(),
            Qt.KeepAspectRatio))
        
    #=========== IMAGE CAPTURE ==========#
    def capture_image(self, capture_dir = None, filename = None):
        # Temporarily stop video stream if running
        self.was_videorunning = False
        if self.is_videorunning:
            self.was_videorunning = True
            self.toggle_videostream()
            QTimer.singleShot(1000, lambda: self._proceed_with_manual_capture(capture_dir, filename))
        else:
            self._proceed_with_manual_capture(capture_dir, filename)
    
    def _proceed_with_manual_capture(self, capture_dir, filename):
        # Gunakan default jika direktori tidak dicustom
        if capture_dir is None:
            capture_dir = self.capture_dir
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"capture_{timestamp}.jpg"
        
        # Start capture thread
        self.capture_thread = ImageThread(self.SERVER_IP, self.IMAGE_PORT, capture_dir, filename)
        self.capture_thread.status_signal.connect(self.handle_capture_status_signal)
        # with self.capture_done_lock:
        #     self.capture_done_flag = False
        self.capture_thread.start()   

    def handle_capture_status_signal(self, status, response):
        # Update status signal from thread into activity log
        if status == True:
            self.log_activity(f"INFO - {response}")
        else:
            self.log_activity(f"ERROR - {response}")
        
        self.capture_thread = None 
        # # Gunakan lock karena flag diakses juga oleh AcqWindow
        # with self.capture_done_lock:
        #     self.capture_done_flag = True

        # Restart video stream if it was running
        self.startvideoButton.setEnabled(True)
        if self.was_videorunning:
            if not self.is_videorunning: # Check if it's still stopped
                self.toggle_videostream() # Restart it
            self.was_videorunning = False

    # --- Methods for Persistent Image Socket for Acquisition ---
    @pyqtSlot()
    def open_persistent_image_socket(self):
        if self.persistent_image_socket and self.persistent_image_socket.is_connected:
            self.log_activity("INFO - Persistent image socket already open.")
            return

        self.persistent_image_socket = SocketClass(self.SERVER_IP, self.IMAGE_PORT, 
                                                   socket_name="PERSISTENT_IMAGE", timeout=10.0)
        success, response = self.persistent_image_socket.connect()
        if success:
            self.log_activity(f"INFO - {response}")
        else:
            self.log_activity(f"ERROR - {response}")
            self.persistent_image_socket = None # Clear if connection failed

    @pyqtSlot()
    def close_persistent_image_socket(self):
        if self.persistent_image_socket:
            success, response = self.persistent_image_socket.disconnect()
            if success:
                self.log_activity(f"INFO - Persistent image socket closed.")
            else:
                self.log_activity(f"ERROR - Error closing persistent image socket: {response}")
            self.persistent_image_socket = None
        else:
            self.log_activity("INFO - No persistent image socket to close.")

    def capture_image_persistent_acq(self, capture_dir, filename):
        """Captures an image using the persistent_image_socket."""
        if not self.persistent_image_socket or not self.persistent_image_socket.is_connected:
            msg = "Persistent image socket not connected for acquisition."
            self.log_activity(f"ERROR - {msg}")
            return False, msg

        try:
            # Send CAPTURE command
            success, response = self.persistent_image_socket.send("CAPTURE")
            if not success:
                self.log_activity(f"ERROR - Persistent send error: {response}")
                return False, f"Send error: {response}"

            # Receive Image Size data (8 bytes for size or error marker)
            success, resp_msg_size, size_data = self.persistent_image_socket.receive(8)
            if not success:
                self.log_activity(f"ERROR - Persistent size receive error: {resp_msg_size}")
                return False, f"Size receive error: {resp_msg_size}"

            if size_data.startswith(b"ERROR.."): # Check for server-side error
                _, _, error_detail_data = self.persistent_image_socket.receive(1024, expect_response=False)
                error_msg = "Server error during capture."
                if error_detail_data:
                    try:
                        error_msg = f"Server error: {error_detail_data.decode('utf-8', errors='strict').strip()}"
                    except UnicodeDecodeError:
                         error_msg = f"Server error (non-utf8): {error_detail_data.hex()}"
                self.log_activity(f"ERROR - {error_msg}")
                return False, error_msg
            
            try:
                img_size = int.from_bytes(size_data, byteorder='big')
            except Exception as e:
                self.log_activity(f"ERROR - Invalid image size data received: {e} ({size_data.hex()})")
                return False, f"Invalid image size data: {size_data.hex()}"

            # Receive Image data
            received_data = b''
            while len(received_data) < img_size:
                chunk_size = min(4096, img_size - len(received_data))
                success, resp_msg_chunk, chunk = self.persistent_image_socket.receive(chunk_size)
                if not success or not chunk: # Ensure chunk is not None or empty
                    msg = f"Persistent image data receive error: {resp_msg_chunk or 'No chunk/error'}"
                    self.log_activity(f"ERROR - {msg}. Received {len(received_data)} of {img_size} bytes.")
                    return False, msg
                received_data += chunk
            
            if len(received_data) != img_size:
                msg = f"Incomplete image data. Expected {img_size}, got {len(received_data)}"
                self.log_activity(f"ERROR - {msg}")
                return False, msg

            # Save Image to file
            save_path = os.path.join(capture_dir, filename)
            try:
                with open(save_path, 'wb') as f:
                    f.write(received_data)
                # self.log_activity(f"INFO - Image saved to {save_path} via persistent socket.") # Can be too verbose
                return True, f"Image saved to {save_path}"
            except IOError as e: # More specific exception for file op
                self.log_activity(f"ERROR - Failed to save image to {save_path}: {e}")
                return False, f"Failed to save image: {e}"

        except socket.timeout:
            self.log_activity("ERROR - Socket timeout during persistent image capture.")
            # Consider if the socket is still usable or should be closed/reopened.
            # For now, return error; ScanThread might retry or stop.
            self.close_persistent_image_socket() # Close on timeout, likely unstable
            return False, "Socket timeout"
        except Exception as e:
            self.log_activity(f"ERROR - Capture error via persistent socket: {str(e)}")
            self.close_persistent_image_socket() # Close on other major errors
            return False, f"Capture error: {str(e)}"
        
    #=========== POSITION ==========#
    def update_position(self, status_data, position_data):
        # Update current position
        self.CURRENT_POSITION.update(position_data)
        # Gunakan threading lock karena current status juga diakses oleh AcqWindow
        with self.current_status_lock:
            self.CURRENT_STATUS = status_data

        # Update UI labels
        if 'X' in position_data:
            self.xposLabel.setText(f"{position_data['X']:.2f}")
        if 'Y' in position_data:
            self.yposLabel.setText(f"{position_data['Y']:.2f}")
        if 'Z' in position_data:
            self.zposLabel.setText(f"{position_data['Z']:.2f}")

        self.statusInput.setText(status_data)

    def jog_move(self, axis, direction):
        if axis not in self.CURRENT_POSITION:
            self.log_activity(f"ERROR - Current position for {axis} not available")
            return
        
        current_pos = float(self.CURRENT_POSITION[axis])
        step = self.stepInput.value() * direction
        target_pos = current_pos + step
        
        # Check movement boundaries
        min_boundary = self.MOVE_BOUNDARY[axis]['min']
        max_boundary = self.MOVE_BOUNDARY[axis]['max']
        
        if min_boundary <= target_pos <= max_boundary:
            command = f"JOG {axis} {step}"
            success, response = self.send_command(command)
            if success:
                self.log_activity(f"INFO - {response}")
            else:
                self.log_activity(f"ERROR - {response}")
        else:
            self.log_activity(f"ERROR - {axis}-axis target {target_pos:.2f} out of bounds")

    def position_move(self, target):
        """Move to absolute position (X, Y, Z)"""
        x, y, z = target
        
        # Check all axes are within boundaries
        if (self.MOVE_BOUNDARY['X']['min'] <= x <= self.MOVE_BOUNDARY['X']['max'] and
            self.MOVE_BOUNDARY['Y']['min'] <= y <= self.MOVE_BOUNDARY['Y']['max'] and
            self.MOVE_BOUNDARY['Z']['min'] <= z <= self.MOVE_BOUNDARY['Z']['max']):
            
            command = f"MOVE X{x} Y{y} Z{z}"
            success, response = self.send_command(command)
            if success:
                self.log_activity(f"INFO - {response}")
            else:
                self.log_activity(f"ERROR - {response}")
        else:
            self.log_activity(f"ERROR - Target position ({x:.2f}, {y:.2f}, {z:.2f}) out of bounds")

    #========== ACQWINDOW FUNC ==========#
    def is_motor_idle(self):
        with self.current_status_lock:
            return self.CURRENT_STATUS == "Idle"

    #========== GENERAL ==========#    
    def handle_status_signal(self, status, response):
        # Update status signal from thread into activity log
        if status == True:
            self.log_activity(f"INFO - {response}")
        else:
            self.log_activity(f"ERROR - {response}")

    def log_activity(self, response):
        row_position = self.activityTable.rowCount()
        self.activityTable.insertRow(row_position)

        time_item = QTableWidgetItem(datetime.now().strftime("%H:%M:%S")) 
        response_item = QTableWidgetItem(response)

        self.activityTable.setItem(row_position, 0, time_item)
        self.activityTable.setItem(row_position, 1, response_item)

        self.activityTable.scrollToBottom()

    def open_acquisition_window(self):
        """Open the acquisition window"""
        # if self.acq_window is None:
        #     current_pos_copy = self.CURRENT_POSITION.copy()
        #     self.acq_window = AcqWindow(current_pos_copy, parent=self) 
        #     self.acq_window.show()
        # else: # If already exists and visible
        #     self.acq_window.raise_()
        #     self.acq_window.activateWindow()

        """Open the acquisition window"""
        if self.acq_window is None:
            self.acq_window = AcqWindow(self.CURRENT_POSITION, parent=self)
        self.acq_window.show()
        self.acq_window.raise_()
        self.acq_window.activateWindow()

    def closeEvent(self, event):
        # Stop all threads
        if self.video_thread:
            self.video_thread.stop()
        
        if self.position_thread:
            self.position_thread.stop()
        
        # Close persistent socket if open
        self.close_persistent_image_socket()
        
        self.log_activity("INFO - Disconnecting control socket...")
        self.control_socket.disconnect()
        
        logger.info("Client application closed.")
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Client()
    window.show()
    sys.exit(app.exec_())