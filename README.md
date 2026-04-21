# CNC Microscope Control & Image Processing
A Python-based system to control a CNC 1610 (Woodpecker GRBL 0.9) and RPi HQ Camera via Raspberry Pi for automated image aquisition.

## System Configuration
<img width="1193" height="806" alt="rancangan_hardware" src="https://github.com/user-attachments/assets/234588c5-8f12-49a2-95b3-491d6702455f" />
The system uses a Raspberry Pi to bridge the Woodpecker CNC board and the HQ Camera. Control is handled via a wireless TCP/UDP connection, allowing the user to operate the whole system through a remote GUI.

## Setup & Installation
1. **Local PC**: Install all files in ```Final Program``` in Python environment, except ```serverside_copy4.py``` file.
2. **Raspberry Pi**: Install ```serverside_copy4.py``` file in Python environment.
3. Connect the Raspberry Pi and local computer in the same Wi-Fi network.
4. Run the ```serverside_copy4.py``` program in the Raspberry Pi first.
5. Run the ```clientside_copy5.py``` program in the local computer. The GUI will launch and attempt to connect to the Raspberry Pi.

## Features
<img width="1779" height="752" alt="GUI_combined" src="https://github.com/user-attachments/assets/e32ea1c9-e025-4da8-8cfb-c11bb7e2c0dd" />

  * Live Capture: Real-time camera preview & digital zoom
  * Image capture: Capture high-resolution image from camera
  * Position Control: Jog XYZ position and monitor relative lens position
  * Auto Acquisition: Automatically capture consecutive images based on programmed XYZ coordinates.
