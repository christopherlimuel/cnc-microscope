from PyQt5.QtWidgets import QGraphicsScene, QGraphicsRectItem, QWidget, QApplication, QGraphicsEllipseItem, QGraphicsLineItem
from PyQt5.QtGui import QBrush, QPen, QFont
from PyQt5.QtCore import Qt, QRectF, QTimer, pyqtSignal, pyqtSlot, QThread
from PyQt5 import uic
from datetime import datetime
import sys
import time
import os

class ScanThread(QThread):
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()
    status_signal = pyqtSignal(bool, str) # (success, response)
    request_open_persistent_image_socket = pyqtSignal() # New signal
    request_close_persistent_image_socket = pyqtSignal() # New signal

    def __init__(self, parent_gui, path):
        super().__init__()
        self.parent_gui = parent_gui
        self.path_coordinate = path
        self._pause = False
        self._stop = False

    def run(self):
        total = len(self.path_coordinate)
        # Direktori
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        scan_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"Scan_{timestamp}")
        os.makedirs(scan_dir, exist_ok=True)

        self.request_open_persistent_image_socket.emit()
        # Small delay to allow socket to open, or implement a wait/confirm mechanism if needed
        time.sleep(5) 

        try:
            for idx, step in enumerate(self.path_coordinate):
                if self._stop:
                    self.status_signal.emit(True, "Scan stopped by user")
                    break
                if self._pause:
                    pause_time = time.time()
                    self.status_signal.emit(True, "Scan paused")
                    while self._pause and not self._stop:
                        time.sleep(0.1)
                    self.status_signal.emit(True, "Scan resumed")
        
                self.progress_signal.emit(int((idx+1)/total*100))

                self.parent_gui.position_move((step['x'], step['y'], step['z']))
                # time.sleep(2)
                # self.wait_motor_idle()
                # Wait for motor to become idle
                while not self.parent_gui.is_motor_idle(): #
                    if self._stop:
                        break
                    time.sleep(0.05) # Poll status frequently but allow GUI to remain responsive
                if self._stop:
                    self.status_signal.emit(True, "Scan stopped by user while waiting for motor.")
                    break

                if not step['is_dummy']:
                    # filename = f"{step['x']}_{step['y']}_{step['z']}.jpg"
                    filename = f"{idx:03d}_{step['x']:.2f}_{step['y']:.2f}_{step['z']:.2f}.jpg"
                    self.parent_gui.capture_image(scan_dir, filename)
                    # Use new persistent capture method
                    capture_success, capture_message = self.parent_gui.capture_image_persistent_acq(scan_dir, filename)
                    if not capture_success:
                        self.status_signal.emit(False, f"Capture failed for {filename}: {capture_message}")
                    time.sleep(0.5)

            self.status_signal.emit(True, "Scan completed")
        except Exception as e:
            self.status_signal.emit(False, f"Scan error: {e}")
            self.finished_signal.emit()
        finally:
            self.request_close_persistent_image_socket.emit()
            self.finished_signal.emit()
    
    def wait_capture_done(self):
        while not self.parent_gui.capture_done_flag:
            time.sleep(0.1)
        self.parent_gui.capture_done_flag = False

        while True:
            with self.parent_gui.capture_done_lock:
                if self.parent_gui.capture_done_flag == True:
                    self.parent_gui.capture_done_flag = False
            time.sleep(0.1)
    
    def wait_motor_idle(self):
        while not self.parent_gui.is_motor_idle():
            if self._stop:
                break
            time.sleep(0.1)

    def pause(self):
        self._pause = True

    def resume(self):
        self._pause = False
    
    def stop(self):
        self._stop = True
        self._pause = False

class AcqWindow(QWidget):
    def __init__(self, current_position, parent=None):
        super().__init__()
        uic.loadUi("acqlayout.ui", self)
        # self.current_position = {'X': '0.00', 'Y': '0.00', 'Z': '0.00'}
        self.current_position = current_position
        self.mainwindow = parent
        self.scanthread = None
        self.backlash = 1.0 # backlash correction (mm)
        self.initial_video_state_was_running = False

        #========== Tile Graphics ==========#
        self.scene = QGraphicsScene()
        self.graphicsViewGrid.setScene(self.scene)
        self.tile_items = []  # Menyimpan tile agar bisa diubah warnanya
        self.tile_size = 50
        self.spacing = 2

        self.xtiles = self.xtilesInput.value()
        self.ytiles = self.ytilesInput.value()
        self.xystepsize = self.xystepInput.value()
        self.ztiles = self.ztilesInput.value()
        self.zstepsize = self.zstepInput.value()

        self.xtilesInput.valueChanged.connect(self.draw_grid)
        self.ytilesInput.valueChanged.connect(self.draw_grid)
        self.startingpointInput.currentTextChanged.connect(self.draw_grid)
        self.xystepInput.valueChanged.connect(self.draw_grid)
        self.ztilesInput.valueChanged.connect(self.draw_grid)
        self.zstepInput.valueChanged.connect(self.draw_grid)

        self.startButton.clicked.connect(self.start_acquisition)
        self.pauseButton.clicked.connect(self.pause_acquisition)
        self.stopButton.clicked.connect(self.stop_acquisition)
        self.draw_grid()

    def draw_grid(self):
        self.scene.clear()
        self.tile_items.clear()
        self.xtiles = self.xtilesInput.value()
        self.ytiles = self.ytilesInput.value()
        self.startingpoint = self.startingpointInput.currentText()       
        self.xystepsize = self.xystepInput.value()
        self.ztiles = self.ztilesInput.value()
        self.zstepsize = self.zstepInput.value() 

        view_width = self.graphicsViewGrid.viewport().width()
        view_height = self.graphicsViewGrid.viewport().height()

        grid_width = self.xtiles * self.tile_size + (self.xtiles - 1) * self.spacing
        grid_height = self.ytiles * self.tile_size + (self.ytiles - 1) * self.spacing
        # Scale otomatis jika
        scale_needed = grid_width > view_width or grid_height > view_height

        for y in range(self.ytiles):
            row = []
            for x in range(self.xtiles):
                rect = QRectF(
                    x * (self.tile_size + self.spacing),
                    y * (self.tile_size + self.spacing),
                    self.tile_size,
                    self.tile_size
                )
                item = QGraphicsRectItem(rect)
                item.setBrush(QBrush(Qt.lightGray))
                item.setPen(QPen(Qt.black))
                self.scene.addItem(item)
                row.append(item)
            self.tile_items.append(row)
        self.scene.setSceneRect(self.scene.itemsBoundingRect())
        if scale_needed:
            self.graphicsViewGrid.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

        #========== Generate Path Tile ==========#
        if "Top" in self.startingpoint:
            row_idx = list(range(self.ytiles))
        else:
            row_idx = list(reversed(range(self.ytiles)))
        
        left_to_right = "Left" in self.startingpoint
        self.path_tiles= []
        for z in range(self.ztiles):
            for i, y in enumerate(row_idx):
                if i % 2 == 0:  # baris genap (0, 2, 4, ...)
                    if left_to_right:
                        col_idx = range(self.xtiles)
                    else:
                        col_idx = reversed(range(self.xtiles))
                else:  # baris ganjil
                    if left_to_right:
                        col_idx = reversed(range(self.xtiles))
                    else:
                        col_idx = range(self.xtiles)
                for x in col_idx:
                    self.path_tiles.append((x, y, z))

        for idx, (x, y, z) in enumerate(self.path_tiles):
            is_start = (idx == 0)
            is_end = (idx == len(self.path_tiles) - 1)
            self.draw_tile(x, y, is_start=is_start, is_end=is_end)

        #========== Generate Path Coordinate ==========#
        self.path_coordinate = []
        x0 = float(self.current_position['X'])
        y0 = float(self.current_position['Y'])
        z0 = float(self.current_position['Z'])

        # Backlash correction Y
        if self.backlash > 0:
            self.path_coordinate.append({'x': x0, 'y': y0 - self.backlash, 'z': z0, 'is_dummy': True})

        prev_direction = None
        for idx, (x, y, z) in enumerate(self.path_tiles):
            x_real = x0 + x * self.xystepsize
            y_real = y0 + y * self.xystepsize
            z_real = z0 + z * self.zstepsize

            if idx > 0:
                prev_x, prev_y, _ = self.path_tiles[idx - 1]
                direction = x - prev_x
                if direction != 0 and prev_direction is not None and direction != prev_direction:
                    backlash_offset = self.backlash if direction > 0 else -self.backlash
                    self.path_coordinate.append({'x': x_real + backlash_offset, 'y': y_real, 'z': z_real, 'is_dummy': True})
                prev_direction = direction
            else:
                prev_direction = 0

            self.path_coordinate.append({'x': x_real, 'y': y_real, 'z': z_real, 'is_dummy': False})

        # FOR BACKLASH TESTING
        # self.path_coordinate = []
        # for idx, n in enumerate(range(5)):
        #     for i in range(10):
        #         if idx%2 == 0:
        #             y = y0 - 0.05
        #             y0 = y
        #         else:
        #             y = y0 + 0.05
        #             y0 = y
        #         self.path_coordinate.append({'x': x0, 'y': y, 'z': z0, 'is_dummy': False})

        print(self.path_coordinate)
        # for (x, y, z) in self.path_tiles:
        #     x_path_coordinate = float(self.current_position['X']) + x*self.xystepsize
        #     y_path_coordinate = float(self.current_position['Y']) + y*self.xystepsize
        #     z_path_coordinate = float(self.current_position['Z']) + z*self.zstepsize
        #     xyz_path_coordinate = (x_path_coordinate, y_path_coordinate, z_path_coordinate)
        #     self.path_coordinate.append(xyz_path_coordinate)

        #========== Corner Coordinates ==========#
        w = (self.xtiles - 1) * self.xystepsize
        h = (self.ytiles - 1) * self.xystepsize

        if self.startingpoint == "Top Left":
            tl = (x0, y0)
            tr = (x0 + w, y0)
            bl = (x0, y0 + h)
            br = (x0 + w, y0 + h)
        elif self.startingpoint == "Top Right":
            tr = (x0, y0)
            tl = (x0 - w, y0)
            br = (x0, y0 + h)
            bl = (x0 - w, y0 + h)
        elif self.startingpoint == "Bottom Left":
            bl = (x0, y0)
            br = (x0 + w, y0)
            tl = (x0, y0 - h)
            tr = (x0 + w, y0 - h)
        elif self.startingpoint == "Bottom Right":
            br = (x0, y0)
            bl = (x0 - w, y0)
            tr = (x0, y0 - h)
            tl = (x0 - w, y0 - h)

        self.topleftLabel.setText(f"Top left: {tl}")
        self.toprightLabel.setText(f"Top right: {tr}")
        self.bottomleftLabel.setText(f"Bottom left: {bl}")
        self.bottomrightLabel.setText(f"Bottom right: {br}")


    def draw_tile(self, x, y, is_start=False, is_end=False):
        tile_size = self.tile_size
        spacing = self.spacing

        rect_x = x * (tile_size + spacing)
        rect_y = y * (tile_size + spacing)

        # Gambar marker untuk start
        if is_start:
            marker = QGraphicsEllipseItem(
                rect_x + tile_size / 4,
                rect_y + tile_size / 4,
                tile_size / 2,
                tile_size / 2
            )
            marker.setBrush(QBrush(Qt.green))
            marker.setPen(QPen(Qt.darkGreen, 2))
            self.scene.addItem(marker)              

        # Gambar marker untuk end
        if is_end:
            size = tile_size / 2
            offset = tile_size / 4
            # Cross (X)
            line1 = QGraphicsLineItem(rect_x + offset, rect_y + offset,
                                    rect_x + offset + size, rect_y + offset + size)
            line2 = QGraphicsLineItem(rect_x + offset + size, rect_y + offset,
                                    rect_x + offset, rect_y + offset + size)
            pen = QPen(Qt.red, 5)
            line1.setPen(pen)
            line2.setPen(pen)
            self.scene.addItem(line1)
            self.scene.addItem(line2)
            
    def start_acquisition(self):
        if not self.scanthread or not self.scanthread.isRunning():
            self.handle_status_signal(True, "Starting Acquisition")
            self.progressBar.setValue(0)

            if not self.path_coordinate:
                self.handle_status_signal(False, "No scan path defined")
                return
            
            # Stop video stream if it's running
            if self.mainwindow:
                self.initial_video_state_was_running = self.mainwindow.is_videorunning
                if self.initial_video_state_was_running:
                    self.mainwindow.toggle_videostream() # Send command to stop video
                    # Add a small delay to ensure video stream is stopped on server
                    # This is important as server might reject image capture if stream is active
                    QTimer.singleShot(1000, self._proceed_with_scan_setup) # Proceed after 1 sec
                else:
                    self._proceed_with_scan_setup()
            else:
                self._proceed_with_scan_setup()
    
        elif self.scanthread and self.scanthread.isRunning() and self.scanthread._pause: # Check if paused
            self.scanthread.resume()
            self.startButton.setEnabled(False) # Resume pressed, disable resume
            self.pauseButton.setEnabled(True)  # Enable pause again
            self.stopButton.setEnabled(True)
            self.handle_status_signal(True, "Resuming acquisition")

    def _proceed_with_scan_setup(self):
        # This method contains the original logic from start_acquisition after video handling
        self.scanthread = ScanThread(self.mainwindow, self.path_coordinate)
        self.scanthread.progress_signal.connect(self.setprogressBar)
        self.scanthread.finished_signal.connect(self.acquisition_done)
        self.scanthread.status_signal.connect(self.handle_status_signal)
        
        # Connect new signals for persistent image socket
        if self.mainwindow:
            self.scanthread.request_open_persistent_image_socket.connect(self.mainwindow.open_persistent_image_socket)
            self.scanthread.request_close_persistent_image_socket.connect(self.mainwindow.close_persistent_image_socket)

        self.scanthread.start()

        if self.mainwindow:
            self.mainwindow.rightColumn.setEnabled(False)
        self.startButton.setText("Resume") # Text changes to Resume
        self.startButton.setEnabled(False) # Disable Resume until pause
        self.pauseButton.setEnabled(True)
        self.stopButton.setEnabled(True)

    def setprogressBar(self, progress):
        self.progressBar.setValue(progress)

    def acquisition_done(self):
        if self.mainwindow:
            self.mainwindow.rightColumn.setEnabled(True)
        self.startButton.setEnabled(True)
        self.startButton.setText("Start")
        self.pauseButton.setEnabled(False)
        self.stopButton.setEnabled(False)
        self.handle_status_signal(True, "Acquisition completed")

    def pause_acquisition(self):
        if self.scanthread and self.scanthread.isRunning():
            self.scanthread.pause()
            self.startButton.setEnabled(True)
            self.startButton.setText("Resume")
            self.pauseButton.setEnabled(False)
            self.handle_status_signal(True, "Acquisition paused")
        else:
            self.handle_status_signal(False, "No active scan process to be paused")
    
    def stop_acquisition(self):
        if self.scanthread and self.scanthread.isRunning():
            self.scanthread.stop()
            self.handle_status_signal(True, "Acquisition stopped")
        else:
            self.handle_status_signal(False, "No active scan process to be stopped")
    
    def handle_status_signal(self, status, response):
        if self.mainwindow:
            if status:
                self.mainwindow.log_activity(f"INFO - {response}")
            else:
                self.mainwindow.log_activity(f"ERROR - {response}")

# For standalone testing
# if __name__ == "__main__":
#     app = QApplication(sys.argv)
#     window = AcqWindow({'X':10.0, 'Y':10.0, 'Z':-5.0})
#     window.show()
#     sys.exit(app.exec_())
        