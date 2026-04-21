import socket
import threading
import time
from datetime import datetime
import os
import serial
import logging
import subprocess
import re
import sys
import json
from typing import Tuple, Dict, Any, Optional, Union

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("client.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class ServerConfig:
    # Network settings
    HOST = '0.0.0.0'
    CONTROL_PORT = 7070
    VIDEO_PORT = 7071
    IMAGE_PORT = 7072
    POSITION_PORT = 7073
    
    # Camera settings
    VIDEO_WIDTH = 1280
    VIDEO_HEIGHT = 720
    VIDEO_QUALITY = 80
    VIDEO_FPS = 30
    IMAGE_WIDTH = 1920
    IMAGE_HEIGHT = 1080
    
    # Serial settings
    SERIAL_PORT = '/dev/ttyUSB0'
    SERIAL_BAUDRATE = 115200
    SERIAL_FEED_RATE = 1000
    
    # Application settings
    POSITION_UPDATE_INTERVAL = 0.5
    POSITION_STABILITY_COUNT = 5  # Number of identical position readings to consider motor stopped
    
    # Directories
    @classmethod
    def get_capture_dir(cls):
        """Return the directory for storing captured images"""
        capture_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
        os.makedirs(capture_dir, exist_ok=True)
        return capture_dir
    
class SocketClass:
    def __init__(self, host, port, name=""):
        self.host = host
        self.port = port
        self.name = name
        self.socket = None
        
    def bind(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            logger.info(f"{self.name} server listening on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"{self.name} server binding error: {str(e)}")
            raise

    def shutdown(self):
        if self.socket:
            try:
                self.socket.close()
                logger.info(f"{self.name} server shut down")
            except Exception as e:
                logger.error(f"Error shutting down {self.name} server: {str(e)}")

#========== VIDEO STREAM ==========#
class CameraManager:
    def __init__(self):
        self.video_process = None
        self.is_videorunning = False
        self.process_lock = threading.Lock()
        self.capture_dir = ServerConfig.get_capture_dir()

    def start_video_stream(self, client_ip):
        with self.process_lock:
            if self.is_videorunning and self.video_process is not None:
                return False, "Video stream already running"
            try:
                cmd = [
                    'libcamera-vid',
                    '--width', str(ServerConfig.VIDEO_WIDTH),
                    '--height', str(ServerConfig.VIDEO_HEIGHT),
                    '--framerate', str(ServerConfig.VIDEO_FPS),
                    '--codec', 'mjpeg',
                    '--quality', str(ServerConfig.VIDEO_QUALITY),
                    '--inline',  # H264 headers in stream
                    '-t', '0',   # No timeout
                    '-o', f'udp://{client_ip}:{ServerConfig.VIDEO_PORT}'
                ]
                
                self.video_process = subprocess.Popen(cmd)
                self.is_streaming = True
                logger.info("Video stream started")
                return True, "Video stream started"
            
            except Exception as e:
                logger.error(f"Error starting video stream: {str(e)}")
                self.is_streaming = False
                return False, f"Error starting video stream: {str(e)}"
            
    def stop_video_stream(self):
        with self.process_lock:
            if not self.is_streaming or self.video_process is None:
                return False, "Video stream is not running"
                
            try:
                self.video_process.terminate()
                self.video_process.wait(timeout=5)
                logger.info("Video stream stopped")
                self.is_streaming = False
                self.video_process = None
                return True, "Video stream stopped"
                
            except subprocess.TimeoutExpired:
                self.video_process.kill()
                logger.warning("Video stream stopped forcefully")
                self.is_streaming = False
                self.video_process = None
                return True, "Video stream stopped forcefully"
                
            except Exception as e:
                logger.error(f"Error stopping video stream: {str(e)}")
                return False, f"Error stopping video stream: {str(e)}"

    def capture_image(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"capture_{timestamp}.jpg"
        filepath = os.path.join(self.capture_dir, filename)
        with self.process_lock:
            if self.is_streaming:
                logger.error("Cannot capture image while video is streaming")
                return None
                
            try:
                cmd = [
                    'libcamera-still',
                    '--width', str(ServerConfig.IMAGE_WIDTH),
                    '--height', str(ServerConfig.IMAGE_HEIGHT),
                    '--output', filepath,
                    '--nopreview'
                ]
                
                capture_process = subprocess.run(cmd, check=True, timeout=10)
                logger.info(f"Image captured: {filepath}")
                return filepath
                
            except Exception as e:
                logger.error(f"Error capturing image: {str(e)}")
                return None
            
    def is_video_running(self) -> bool:
        """Check if video streaming is active
        
        Returns:
            True if video is streaming, False otherwise
        """
        with self.process_lock:
            return self.is_streaming
            
    def shutdown(self) -> None:
        """Stop any active processes"""
        self.stop_video_stream()

class ImageServer(SocketClass):
    def __init__(self, host, port, camera_manager):
        super().__init__(host, port, "IMAGE")
        self.camera_manager = camera_manager

    def start(self) -> None:
        """Start the image server"""
        self.bind()
        self.running = True
        
        try:
            while self.running:
                client_socket, client_addr = self.socket.accept()
                logger.info(f"New image connection from {client_addr}")
                
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, client_addr)
                )
                client_thread.daemon = True
                client_thread.start()
                
        except Exception as e:
            if self.running:  # Only log if not a normal shutdown
                logger.error(f"Image server error: {str(e)}")
        finally:
            self.shutdown()
    
    def _handle_client(self, client_socket: socket.socket, client_addr: Tuple[str, int]) -> None:
        try:
            data = client_socket.recv(1024)
            
            if data and data.decode('utf-8').strip().upper() == "CAPTURE":
                # Check if video is running
                if self.camera_manager.is_video_running():
                    error_msg = "Stop video stream before capture"
                    client_socket.sendall(b"ERROR..".ljust(8, b'\0'))
                    logger.error(error_msg)
                    client_socket.sendall(error_msg.encode('utf-8'))
                else:
                    self._handle_image_transfer(client_socket)
            else:
                error_msg = "Command not valid"
                client_socket.sendall(b"ERROR..".ljust(8, b'\0'))
                client_socket.sendall(error_msg.encode('utf-8'))
                
        except Exception as e:
            logger.error(f"Error handling image connection: {str(e)}")
        finally:
            client_socket.close()
            logger.info(f"Image connection from {client_addr} closed")
    
    def _handle_image_transfer(self, client_socket: socket.socket) -> None:
        try:
            image_path = self.camera_manager.capture_image()
            
            if not image_path or not os.path.exists(image_path):
                error_msg = "Failed to capture image"
                client_socket.sendall(b"ERROR..".ljust(8, b'\0'))
                client_socket.sendall(error_msg.encode('utf-8'))
                return
                
            file_size = os.path.getsize(image_path)
            client_socket.sendall(file_size.to_bytes(8, byteorder='big'))
            
            with open(image_path, 'rb') as f:
                while True:
                    data = f.read(4096)
                    if not data:
                        break
                    client_socket.sendall(data)
                    
            logger.info(f"Captured image sent: {image_path}")
            
        except Exception as e:
            error_msg = f"Image transfer failed: {str(e)}"
            client_socket.sendall(b"ERROR..".ljust(8, b'\0'))
            client_socket.sendall(error_msg.encode('utf-8'))
            logger.error(error_msg)

#========== POSITION & MOVEMENT ==========#
class SerialManager:
    def __init__(self, port, baudrate):
        self.port = port
        self.baudrate = baudrate
        self.lock = threading.Lock()

    def connect(self):
        try:
            self.conn = serial.Serial(
                port=self.port, 
                baudrate=self.baudrate,
                timeout=0.1
            )
            time.sleep(1)

            # Wake up GRBL
            with self.lock:
                self.conn.write(b"\r\n\r\n")
                time.sleep(1)
                self.conn.flushInput()
            self.send_gcode("G21")      # set unit to mm
            time.sleep(0.1)
            self.send_gcode("G21")
            logger.info(f"Serial connection established on {self.port}")
            return True, "Serial connection established"
        except Exception as e:
            logger.error(f"Failed to establish serial connection: {str(e)}")
            return False, f"Failed to establish serial connection: {str(e)}"
        
    def disconnect(self):
        if self.conn and self.conn.is_open:
            try:
                self.conn.close()
                logger.info("Serial connection closed")
            except Exception as e:
                logger.error(f"Error closing serial connection: {str(e)}")

    def send_gcode(self, gcode):
        if not self.conn or not self.conn.is_open:
            return False, "Serial connection not established"
        
        try:
            if not gcode.endswith('\n'):
                gcode += '\n'
            
            with self.lock:
                self.conn.write(gcode.encode('utf-8'))
                self.conn.flush()
                
                # Read response from controller
                response = f"{gcode.strip()} >>> No response"
                timeout = time.time() + 5
                while time.time() < timeout:
                    if self.conn.in_waiting:
                        line = self.conn.readline().decode('utf-8').strip()
                        # Response ok
                        if line.lower() == "ok":
                            response = f"{gcode.strip()} >>> OK"
                            break
                    time.sleep(0.1)
                    
            logger.info(response)
            return True, response
        except Exception as e:
            logger.error(f"Error sending G-Code: {str(e)}")
            return False, f"Error sending G-Code: {str(e)}"
        
    def get_position(self):
        if not self.conn or not self.conn.is_open:
            return None
        
        try:
            with self.lock:
                self.conn.reset_input_buffer()
                self.conn.write(b'?\n')
                time.sleep(0.05)
                
                response = ""
                start = time.time()
                while time.time() - start < 0.2:
                    if self.conn.in_waiting > 0:
                        line = self.conn.readline().decode('utf-8').strip()
                        response += line
                        # break
                
                match = re.search(r"<(\w+)[,|]MPos:([-\d.]+),([-\d.]+),([-\d.]+)", response)
                if match:
                    status = match.group(1)
                    x = float(match.group(2))
                    y = float(match.group(3))
                    z = float(match.group(4))
                    return (status, {'X': x, 'Y': y, 'Z': z})
                else:
                    logger.warning(f"Unexpected position format: {response}")
                    return ("Unknown", {'X':0.00, 'Y':0.00, 'Z':0.00})
                    
        except Exception as e:
            logger.warning(f"Error getting position data: {str(e)}")
            return None
        
class MotionController:
    def __init__(self, serial_manager:SerialManager):
        self.serial = serial_manager 
        self.CURRENT_POSITION = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
        self.LAST_POSITION = None
        self.CURRENT_STATUS = "IDLE"
        self.position_lock = threading.Lock()
        self.running = True

        self.position_thread = threading.Thread(target=self.position_monitor_loop)
        self.position_thread.daemon = True
        self.position_thread.start()

    def position_monitor_loop(self):
        # Continously asking for status and position
        while self.running:
            status, position = self.serial.get_position()
            if status and position:
                with self.position_lock:
                    self.CURRENT_POSITION = position
                    self.CURRENT_STATUS = status
            time.sleep(0.1)

    def get_current_position(self):
        with self.position_lock:
            status = self.CURRENT_STATUS
            position = self.CURRENT_POSITION.copy()
            return (status, position) # ("IDLE", {'X': 0.0, 'Y': 0.0, 'Z': 0.0})
    
    def move_jog(self, axis, direction):
        if axis not in ['X', 'Y', 'Z']:
            return False, "Invalid axis specified"
        
        try:
            direction = float(direction)
            gcode = f"G91\nG0 {axis}{direction} F{ServerConfig.SERIAL_FEED_RATE}"
            success, response = self.serial.send_gcode(gcode)
            return success, response
        except Exception as e:
            logger.error(f"Error moving motor: {str(e)}")
            return False, f"Error moving motor: {str(e)}"
        
    def position_move(self, target):
        try:
            target_str = " ".join([f"{axis}{val}" for axis, val in target.items()])
            gcode = f"G90 G0 {target_str} F{ServerConfig.SERIAL_FEED_RATE}"
            success, response = self.serial.send_gcode(gcode)
            return success, response
        except Exception as e:
            logger.error(f"Error moving to absolute position: {str(e)}")
            return False, f"Error moving to absolute position: {str(e)}"

    def shutdown(self):
        self.running = False
        if self.position_thread.is_alive():
            self.position_thread.join(timeout=2.0)

class PositionServer(SocketClass):
    def __init__(self, host, port, motion_controller:MotionController):
        super().__init__(host, port, "POSITION")
        self.motion_controller = motion_controller

    def start(self):
        self.bind()
        self.running = True

        try:
            while self.running:
                position_client, position_addr = self.socket.accept()
                logger.info(f"New position connection from {position_addr}")
                
                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(position_client, position_addr)
                )
                client_thread.daemon = True
                client_thread.start()         
        except Exception as e:
            if self.running:
                logger.error(f"Position server error: {str(e)}")
        finally:
            self.shutdown()

    def handle_client(self, position_client, position_addr):
        stability_counter = 0
        try:
            while self.running:
                status, position = self.motion_controller.get_current_position()
                if status == 'IDLE':
                    stability_counter += 1

                if status != 'IDLE' or stability_counter >= 5:
                    status, position = self.motion_controller.get_current_position()
                    position_str = f"('{status}', {{'X': {position['X']:.2f}, 'Y': {position['Y']:.2f}, 'Z': {position['Z']:.2f}}})"
                    position_client.sendall(position_str.encode('utf-8'))
                    stability_counter = 0 
                
                time.sleep(ServerConfig.POSITION_UPDATE_INTERVAL)
        except Exception as e:
            logger.error(f"Error handling position connection: {str(e)}")
        finally:
            position_client.close()
            logger.info(f"Position connection from {position_addr} closed")


#========== ORGANIZER ==========#
class ControlServer(SocketClass):
    def __init__(self, host, port, motion_controller:MotionController, camera_manager:CameraManager):
        super().__init__(host, port, "CONTROL")
        self.motion_controller = motion_controller
        self.camera_manager = camera_manager

    def start(self):
        self.bind()
        self.running = True

        try:
            while self.running:
                client_socket, client_addr = self.socket.accept()
                logger.info(f"New control connection from {client_addr}")
                
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, client_addr)
                )
                client_thread.daemon = True
                client_thread.start()
                
        except Exception as e:
            if self.running:  # Only log if not a normal shutdown
                logger.error(f"Control server error: {str(e)}")
        finally:
            self.shutdown()

    def _handle_client(self, client_socket: socket.socket, client_addr: Tuple[str, int]) -> None:
        try:
            while self.running:
                data = client_socket.recv(1024)
                if not data:
                    break
                    
                command = data.decode('utf-8')
                response = self._handle_command(command, client_addr[0])
                client_socket.sendall(response.encode('utf-8'))
                
        except Exception as e:
            logger.error(f"Error handling control connection: {str(e)}")
        finally:
            client_socket.close()
            logger.info(f"Control connection from {client_addr} closed")
    
    def _handle_command(self, command: str, client_ip: str) -> str:
        logger.info(f"Received command: {command}")
        
        try:
            cmd_parts = command.strip().split()
            if not cmd_parts:
                return "ERROR - Empty command"
                
            cmd_type = cmd_parts[0].upper()
            
            if cmd_type == "START_VIDEO":
                success, message = self.camera_manager.start_video_stream(client_ip)
                return message
                
            elif cmd_type == "STOP_VIDEO":
                success, message = self.camera_manager.stop_video_stream()
                return message
                
            elif cmd_type == "JOG":
                if len(cmd_parts) >= 3:
                    axis = cmd_parts[1].upper()
                    distance = float(cmd_parts[2])
                    success, message = self.motion_controller.move_jog(axis, distance)
                    return message
                else:
                    return "ERROR - Invalid JOG command format"
                    
            elif cmd_type == "MOVE":
                # Parse positions from command
                positions = {}
                for part in cmd_parts[1:]:
                    if len(part) >= 2 and part[0].upper() in ['X', 'Y', 'Z']:
                        axis = part[0].upper()
                        try:
                            value = float(part[1:])
                            positions[axis] = value
                        except ValueError:
                            return f"ERROR - Invalid position value: {part}"
                
                if positions:
                    success, message = self.motion_controller.position_move(positions)
                    return message
                else:
                    return "ERROR - Invalid MOVE command format"
                    
            else:
                return f"ERROR - Unknown command: {cmd_type}"
                
        except Exception as e:
            logger.error(f"Error processing command: {str(e)}")
            return f"ERROR - Error processing command: {str(e)}"

class Server:
    def __init__(self):
        #========== SYSTEM SETTINGS ==========#
        self.HOST = '0.0.0.0'
        self.CONTROL_PORT = 7070
        self.VIDEO_PORT = 7071
        self.IMAGE_PORT = 7072
        self.POSITION_PORT = 7073

        self.VIDEO_WIDTH = 854
        self.VIDEO_HEIGHT = 480
        self.VIDEO_QUALITY = 80
        self.VIDEO_FPS = 30
        self.IMAGE_WIDTH = 1920
        self.IMAGE_HEIGHT = 1080

        self.SERIAL_PORT = '/dev/ttyUSB0'
        self.SERIAL_BAUDRATE = 115200
        self.SERIAL_FEED_RATE = 1000
        
        self.POSITION_UPDATE_INTERVAL = 0.5
        self.POSITION_STABILITY_COUNT = 5 
        
        self.serial_manager = SerialManager(
            self.SERIAL_PORT, self.SERIAL_BAUDRATE
        )
        
        success, response = self.serial_manager.connect()
        if not success:
            logger.error(response)

        self.motion_controller = MotionController(self.serial_manager)
        self.camera_manager = CameraManager()

        self.control_server = ControlServer(
            ServerConfig.HOST, 
            ServerConfig.CONTROL_PORT,
            self.motion_controller,
            self.camera_manager
        )

        self.image_server = ImageServer(
            ServerConfig.HOST,
            ServerConfig.IMAGE_PORT,
            self.camera_manager
        )
        
        self.position_server = PositionServer(
            ServerConfig.HOST,
            ServerConfig.POSITION_PORT,
            self.motion_controller
        )
        
        # Create threads for servers
        self.control_thread = threading.Thread(target=self.control_server.start)
        self.control_thread.daemon = True
        
        self.image_thread = threading.Thread(target=self.image_server.start)
        self.image_thread.daemon = True
        
        self.position_thread = threading.Thread(target=self.position_server.start)
        self.position_thread.daemon = True
        
        # Set up shutdown flag
        self.running = False

    def start(self) -> None:
        """Start all server threads"""
        logger.info("Starting Raspberry Pi Server")
        self.running = True
        
        # Start all server threads
        self.control_thread.start()
        self.image_thread.start()
        self.position_thread.start()
        
        logger.info("Server running. Press Ctrl+C to exit")
    
    def run(self) -> None:
        """Main application loop"""
        self.start()
        
        try:
            # Keep the main thread alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down server")
        finally:
            self.shutdown()
    
    def shutdown(self) -> None:
        """Shut down all components"""
        self.running = False
        
        # Shut down servers
        self.control_server.shutdown()
        self.image_server.shutdown()
        self.position_server.shutdown()
        
        # Shut down components
        self.camera_manager.shutdown()
        self.motion_controller.shutdown()
        self.serial_manager.disconnect()
        
        logger.info("Server shutdown complete")

def main():
    """Main entry point"""
    server = Server()
    server.run()
    sys.exit(0)

if __name__ == "__main__":
    main()