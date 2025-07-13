#!/usr/bin/env python3

"""
Linux System Monitoring Script with MQTT and Home Assistant Discovery
Monitors: CPU usage/temp, System load, Memory, Disk SMART, Disk I/O
Publishes to MQTT broker with Home Assistant autodiscovery
"""
# TODO:
# - Get OS name from /etc/os-release
# - Add disk temperature sensors
# - Fix paho-mqtt client connection issues
# - Add disk status: active, idle, inactive
# - Add docker container monitoring
import json
import time
import subprocess
import argparse
import signal
import sys
import os
import re
from typing import Dict, List, Tuple
import paho.mqtt.client as mqtt

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Install with: pip install python-dotenv")
    print("Falling back to system environment variables only.")


class LinuxSystemMonitor:
    def __init__(self):
        # Configuration - Load from environment variables with fallbacks
        self.mqtt_broker = os.getenv("MQTT_BROKER", "localhost")
        self.mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
        self.mqtt_user = os.getenv("MQTT_USER", "")
        self.mqtt_pass = os.getenv("MQTT_PASS", "")
        
        # Parse network interfaces from comma-separated string
        interfaces_str = os.getenv("NETWORK_INTERFACES", "lan")
        self.ifs_name = [iface.strip() for iface in interfaces_str.split(",") if iface.strip()]

        
        # Intervals (in seconds) - Load from environment variables
        self.fast_interval = int(os.getenv("FAST_INTERVAL", "10"))
        self.slow_interval = int(os.getenv("SLOW_INTERVAL", "3600"))
        
        # Home Assistant discovery prefix
        self.ha_discovery_prefix = os.getenv("HA_DISCOVERY_PREFIX", "homeassistant")
        
        with open('/etc/hostname', 'r') as f:
            self.hostname = f.read().strip()
        self.mqtt_client_id = f"linux_monitor_{self.hostname}"
        self.device_name = self.hostname
        self.device_id = self.hostname.lower().replace(" ", "_")
        # Dry run mode
        self.dry_run = False
        
        # Global variables
        self.script_start_time = int(time.time())
        self.last_slow_update = 0
        
        # Cache variables
        self.disk_serial_mapping = {}  # Maps serial -> device path
        self.disk_info_cache = {}      # Maps serial -> disk info (name, model, size)
        self.root_disk = None     # Root disk device name (e.g., "/dev/sda1")
        self.root_block = None         # Root block device name
        self.if_statistics = {}
        
        # MQTT client
        self.mqtt_client = None
        
        # Topic storage
        self.topics = {}
        
    def check_dependencies(self) -> bool:
        """Check if required system tools are available"""
        if self.dry_run:
            print("DRY RUN MODE: Skipping dependency checks")
            return True
            
        missing_deps = []
        required_commands = ["smartctl", "sensors", "iostat"]
        
        for cmd in required_commands:
            if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
                if cmd == "smartctl":
                    missing_deps.append("smartmontools")
                elif cmd == "sensors":
                    missing_deps.append("lm-sensors")
                elif cmd == "iostat":
                    missing_deps.append("sysstat")
        
        if missing_deps:
            print(f"Missing dependencies: {', '.join(missing_deps)}")
            print(f"Please install them with: sudo apt-get install {' '.join(missing_deps)}")
            return False
            
        return True
    
    def setup_mqtt(self):
        """Setup MQTT client connection"""
        if self.dry_run:
            return
            
        self.mqtt_client = mqtt.Client(self.mqtt_client_id)
        
        # Add connection callbacks for debugging
        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                print("Connected to MQTT broker successfully")
                # Set a flag to indicate connection is ready
                self.mqtt_connected = True
            else:
                print(f"Failed to connect to MQTT broker: {rc}")
                self.mqtt_connected = False
    
        def on_publish(client, userdata, mid):
            print(f"Message {mid} published successfully")
    
        def on_disconnect(client, userdata, rc):
            print(f"Disconnected from MQTT broker: {rc}")
            self.mqtt_connected = False
    
        self.mqtt_client.on_connect = on_connect
        self.mqtt_client.on_publish = on_publish
        self.mqtt_client.on_disconnect = on_disconnect
        
        if self.mqtt_user:
            self.mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_pass)
        
        # Initialize connection flag
        self.mqtt_connected = False
        
        try:
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
            
            # Wait for connection to establish with timeout
            max_wait = 10  # seconds
            wait_time = 0
            while not self.mqtt_connected and wait_time < max_wait:
                time.sleep(0.5)
                wait_time += 0.5
            
            if not self.mqtt_connected:
                print(f"Failed to connect to MQTT broker within {max_wait} seconds")
                sys.exit(1)
            
        except Exception as e:
            print(f"Failed to connect to MQTT broker: {e}")
            sys.exit(1)
    
    def mqtt_publish(self, topic: str, payload: str, retain: bool = False):
        """Publish MQTT message"""
        if self.dry_run:
            retain_flag = " [RETAINED]" if retain else ""
            print(f"[DRY RUN]{retain_flag} Topic: {topic}")
            print(f"[DRY RUN]{retain_flag} Payload: {payload}")
            print("---")
            return
        cmd = [
            "mosquitto_pub",
            "-h", str(self.mqtt_broker),
            "-p", str(self.mqtt_port),
            "-u", str(self.mqtt_user),
            "-P", str(self.mqtt_pass),
            "-t", str(topic),
            "-m", str(payload)
        ]
        if retain:
            cmd.append("-r")
        self.run_command(cmd)
        # print(f"Running command: {' '.join(cmd)}")
        # if self.mqtt_client and getattr(self, 'mqtt_connected', False):
        #     # Add debugging output
        #     print(f"Publishing to topic: {topic}")
        #     print(f"Payload: {payload[:100]}...")  # Truncate long payloads
        #     result = self.mqtt_client.publish(topic, payload, retain=retain)
        #     if result.rc != mqtt.MQTT_ERR_SUCCESS:
        #         print(f"Failed to publish to {topic}: {result.rc}")
        # else:
        #     print("MQTT client not connected!")
    
    def run_command(self, cmd: List[str], timeout: int = 30) -> str:
        """Run system command and return output"""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return ""
    
    def get_iostat_data(self) -> Tuple[float, Dict[str, Dict[str, float]]]:
        """Get iostat data for CPU and disk metrics using JSON output"""
        cmd = ["iostat", "-d", str(self.fast_interval), "1", "-y", "-c", "-x", "-o", "JSON"]
        output = self.run_command(cmd)
        
        if not output:
            return {}, {}
        
        try:
            data = json.loads(output)
            
            # Navigate to the statistics data
            stats = data["sysstat"]["hosts"][0]["statistics"][0]
            
            # Extract CPU usage (100 - idle)
            cpu_data = stats.get("avg-cpu", {})
            
            # Extract disk data
            disk_data = {}
            for disk in stats.get("disk", []):
                device = disk.get("disk_device", "")
                if device:
                    disk_data[device] = {
                        'read_kbs': disk.get("rkB/s", 0.0),
                        'write_kbs': disk.get("wkB/s", 0.0),
                        'util': disk.get("util", 0.0)
                    }
            
            return cpu_data, disk_data
            
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            print(f"Error parsing iostat JSON output: {e}")
            return 0.0, {}
    
    def get_cpu_temperature(self) -> Dict:
        """Get CPU temperature using JSON output from sensors"""
        # Try sensors command with JSON output first
        output = self.run_command(["sensors", "-j"])
        if output:
            try:
                data = json.loads(output)
                
                # Look for coretemp or similar CPU temperature sensor
                for sensor_name, sensor_data in data.items():
                    if "coretemp" in sensor_name.lower() or "cpu" in sensor_name.lower():
                        # Collect core temperatures
                        core_temps = {}
                        package_temp = None
                        
                        # Look for Package id 0 first (main CPU temp)
                        if "Package id 0" in sensor_data:
                            package_temp = sensor_data["Package id 0"].get("temp1_input", 0.0)
                        
                        # Collect individual core temperatures as array
                        core_temps_array = []
                        core_count = 0
                        
                        # Collect core temperatures in numerical order
                        core_data_dict = {}
                        for core_name, core_data in sensor_data.items():
                            if "Core" in core_name and isinstance(core_data, dict):
                                # Extract core number
                                try:
                                    core_num = int(core_name.split()[-1])  # "Core 0" -> 0
                                    temp_key = next((k for k in core_data.keys() if k.endswith("_input")), None)
                                    if temp_key:
                                        core_data_dict[core_num] = round(core_data[temp_key], 1)
                                        core_count = max(core_count, core_num + 1)
                                except (ValueError, IndexError):
                                    continue
                        
                        # Build array in order (fill missing cores with 0.0)
                        for i in range(core_count):
                            core_temps_array.append(core_data_dict.get(i, 0.0))
                        
                        # Use package temp if available, otherwise average of cores
                        if package_temp is not None:
                            main_temp = package_temp
                        elif core_temps_array:
                            main_temp = sum(core_temps_array) / len(core_temps_array)
                        else:
                            main_temp = 0.0
                        
                        return {
                            "temperature": round(main_temp, 1),
                            "attrs": {
                                "sensor": sensor_name,
                                "cores": core_temps_array  # Array of core temperatures
                            }
                        }
                
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"Error parsing sensors JSON output: {e}")
        
        # Fallback to thermal zone
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000.0
                return {
                    "temperature": round(temp, 1),
                    "attrs": {
                        "sensor": "thermal_zone0",
                        "source": "sysfs"
                    }
                }
        except (FileNotFoundError, ValueError):
            return {
                "temperature": 0.0,
                "attrs": {
                    "sensor": "unknown",
                    "error": "No temperature sensors found"
                }
            }
    
    
    # def get_system_load(self) -> float:
    #     """Get system load average (1 minute)"""
    #     try:
    #         with open('/proc/loadavg', 'r') as f:
    #             return float(f.read().split()[0])
    #     except (FileNotFoundError, ValueError, IndexError):
    #         return 0.0
    
    def get_memory_usage(self) -> Dict:
        """Get memory usage data by parsing free command output"""
        # Use free command with bytes output for precise values
        output = self.run_command(["free", "-b"])
        if output:
            try:
                lines = output.strip().split('\n')
                
                # Parse memory line (second line)
                mem_line = None
                swap_line = None
                
                for line in lines:
                    if line.startswith('Mem:'):
                        mem_line = line
                    elif line.startswith('Swap:'):
                        swap_line = line
                
                # Parse memory data
                mem_data = {"total": 0, "used": 0, "free": 0, "shared": 0, "buff_cache": 0, "available": 0}
                if mem_line:
                    parts = mem_line.split()
                    if len(parts) >= 7:  # Mem: total used free shared buff/cache available
                        mem_data = {
                            "total": int(parts[1]),
                            "used": int(parts[2]),
                            "free": int(parts[3]),
                            "shared": int(parts[4]),
                            "buff_cache": int(parts[5]),
                            "available": int(parts[6])
                        }
                
                # Parse swap data
                swap_data = {"total": 0, "used": 0, "free": 0}
                if swap_line:
                    parts = swap_line.split()
                    if len(parts) >= 4:  # Swap: total used free
                        swap_data = {
                            "total": int(parts[1]),
                            "used": int(parts[2]),
                            "free": int(parts[3])
                        }
                
                # Calculate usage percentages
                mem_usage_percent = (mem_data["used"] / mem_data["total"] * 100.0) if mem_data["total"] > 0 else 0.0
                swap_usage_percent = (swap_data["used"] / swap_data["total"] * 100.0) if swap_data["total"] > 0 else 0.0
                
                return {
                    "mem_usage": round(mem_usage_percent, 1),
                    "mem": mem_data,
                    "swap_usage": round(swap_usage_percent, 1),
                    "swap": swap_data
                }
                
            except (ValueError, IndexError, TypeError, ZeroDivisionError) as e:
                print(f"Error parsing free output: {e}")
        
        # Return empty data if all methods fail
        return {
            "mem_usage": 0.0,
            "mem": {
                "total": 0,
                "used": 0,
                "free": 0,
                "shared": 0,
                "buff_cache": 0,
                "available": 0
            },
            "swap_usage": 0.0,
            "swap": {
                "total": 0,
                "used": 0,
                "free": 0,
            }
        }
    
    def get_memory_info(self) -> str:
        """Get memory usage in human readable format"""
        output = self.run_command(["free", "-h"])
        if output:
            lines = output.split('\n')
            for line in lines:
                if line.startswith('Mem:'):
                    parts = line.split()
                    if len(parts) >= 3:
                        return f"{parts[2]}/{parts[1]}"
        return "0/0"
    
    def get_uptime_hours(self) -> int:
        """Get system uptime in hours"""
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.read().split()[0])
                return int(uptime_seconds / 3600)
        except (FileNotFoundError, ValueError, IndexError):
            return 0
    def get_disk_list(self) -> List[str]:
        """Get list of physical disks (legacy method - returns device paths)"""
        # This method is kept for backward compatibility
        # but internally uses the serial-based mapping
        serials = self.get_disk_list_by_serial()
        return [self.disk_serial_mapping[serial] for serial in serials if serial in self.disk_serial_mapping]

    def get_disk_temperature(self, disk_or_serial: str) -> float:
        """Get disk temperature using smartctl (accepts device path or serial)"""
        # Determine if input is a serial or device path
        if disk_or_serial.startswith('/dev/'):
            disk_path = disk_or_serial
        else:
            disk_path = self.disk_serial_mapping.get(disk_or_serial, "")
            if not disk_path:
                return 0.0
        
        output = self.run_command(["smartctl", "-A", disk_path])
        if output:
            for line in output.split('\n'):
                if "Temperature_Celsius" in line or "Airflow_Temperature_Cel" in line:
                    parts = line.split()
                    if len(parts) >= 10:
                        try:
                            return float(parts[9])
                        except ValueError:
                            continue
        return 0.0
    def get_disk_usage(self, block_path: str) -> Dict:
        """Get disk usage statistics using lsblk with JSON output (accepts device path)"""
        # Use lsblk with JSON output
        output = self.run_command(["lsblk", "-o", "NAME,SIZE,FSUSED,FSUSE%,FSAVAIL,FSTYPE,FSSIZE,MOUNTPOINT", "-J", "-b", block_path])
        if output:
            try:
                data = json.loads(output)
                blockdevices = data.get("blockdevices", [])
                
                if blockdevices and len(blockdevices) > 0:
                    device = blockdevices[0]
                    
                    # Extract usage percentage (remove % sign)
                    fsuse_percent = device.get("fsuse%", "0%")
                    usage_percent = float(fsuse_percent.replace('%', '')) if fsuse_percent and fsuse_percent != "null" else 0.0
                    
                    # Get size values directly in bytes (thanks to -b flag)
                    def safe_int(value):
                        try:
                            return int(value) if value and value != "null" else 0
                        except (ValueError, TypeError):
                            return 0
                    
                    fssize = safe_int(device.get("fssize", "0"))
                    fsused = safe_int(device.get("fsused", "0"))
                    fsavail = safe_int(device.get("fsavail", "0"))
                    
                    return {
                        "usage_percent": usage_percent,
                        "attrs": {
                            "mount_point": device.get("mountpoint", "unmounted") or "unmounted",
                            "total": fssize,
                            "used": fsused,
                            "free": fsavail,
                            "fstype": device.get("fstype", "unknown") or "unknown",
                            "device_name": device.get("name", "unknown"),
                            "total_size": device.get("size", "unknown")
                        }
                    }
                    
            except (json.JSONDecodeError, ValueError, IndexError, KeyError) as e:
                print(f"Error parsing lsblk JSON output for {block_path}: {e}")
        
        return {
            "usage_percent": 0.0,
            "attrs": {
                "mount_point": "unmounted",
                "total": 0,
                "used": 0,
                "free": 0,
                "fstype": "unknown",
                "device_name": "unknown",
                "total_size": "unknown"
            }
        }
    def get_disk_smart(self, disk_or_serial: str) -> Dict:
        """Get disk SMART data using JSON output from smartctl (accepts device path or serial)"""
        # Determine if input is a serial or device path
        if disk_or_serial.startswith('/dev/'):
            disk_path = disk_or_serial
        else:
            disk_path = self.disk_serial_mapping.get(disk_or_serial, "")
            if not disk_path:
                return {}
        
        # Use smartctl with JSON output for comprehensive SMART data
        output = self.run_command(["smartctl", "-A", "-H", "-j", disk_path])
        if not output:
            return {}
        
        try:
            data = json.loads(output)

            # Extract SMART attributes
            smart_attrs = {
                "smart_passed": 1 if data.get("smart_status", {}).get("passed", False) else 0,
                "temperature": data.get("temperature", {}).get("current", 0),
                "power_on_hours": data.get("power_on_time", {}).get("hours", 0),
                "power_cycle_count": data.get("power_cycle_count", 0),
                "attrs":{}
            }
            # Parse SMART attributes table
            smart_table = data.get("ata_smart_attributes", {}).get("table", [])
            for attr in smart_table:
                attr_id = attr.get("id", 0)
                attr_name = attr.get("name", f"attribute_{attr_id}")
                attr_value = attr.get("raw", {}).get("string", 0)
                
                # Use attribute name if known, otherwise use ID
                if "Unknown" in attr_name:
                    key = f"attribute_{attr_id}"
                else:
                    key = attr_name.lower()
                
                # Store both normalized value and raw value
                smart_attrs["attrs"][key] = attr_value
            
            return smart_attrs
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Error parsing smartctl JSON output for {disk_path}: {e}")
            
            return {
            }

    def get_disk_info(self, device_path: str) -> Dict:
        """Get disk info data using JSON output from smartctl (accepts device path)"""

        # Use smartctl with JSON output for comprehensive SMART data
        output = self.run_command(["smartctl", "-i", "-j", device_path])
        if not output:
            return {}
        
        try:
            data = json.loads(output)
            
            # Extract device info
            info = {
                "device_name": data.get("device", {}).get("name", device_path),
                "model_family": data.get("model_family", "Unknown"),
                "model_name": data.get("model_name", "Unknown"),
                "serial_number": data.get("serial_number", "Unknown"),
                "firmware_version": data.get("firmware_version", "Unknown"),
                "user_capacity": data.get("user_capacity", {}).get("bytes", 0),
                "logical_block_size": data.get("logical_block_size", 512),
                "rotation_rate": data.get("rotation_rate", 0),
                "form_factor": data.get("form_factor", {}).get("name", "Unknown"),
                "interface_speed": f"{data.get('interface_speed', {}).get('current', {}).get('string', 'Unknown')} / {data.get('interface_speed', {}).get('max', {}).get('string', 'Unknown')}",
                "smart_available": data.get("smart_support", {}).get("available", False)
            }
            
            return info
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Error parsing smartctl JSON output for {device_path}: {e}")
            
            return {
                "device_name": device_path,
                "firmware_version": "Unknown",
                "user_capacity": 0,
                "logical_block_size": 512,
                "rotation_rate": 0,
                "form_factor": "Unknown",
                "interface_speed": "Unknown / Unknown",
                "smart_available": True
            }

    def get_os_info(self) -> str:
        """Get OS pretty name from /etc/os-release"""
        try:
            with open('/etc/os-release', 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('PRETTY_NAME='):
                        # Remove PRETTY_NAME= and strip quotes
                        value = line.split('=', 1)[1]
                        return value.strip('"').strip("'")
        except (FileNotFoundError, IOError):
            pass
        
        # Fallback to uname if /etc/os-release not available
        return self.run_command(['uname', '-o'])
    def get_hardware_info(self) -> str:
        """Get hardware information using lshw"""
        with open('/proc/cpuinfo') as f:
            for line in f:
                if 'model name' in line:
                    cpu_name = line.strip().split(': ')[1]
                    break
        if cpu_name:
            return cpu_name
        
        return "Hardware information not available"
    
    def setup_discovery(self):
        """Setup Home Assistant discovery configurations"""
        print("Setting up Home Assistant discovery...")
        self.topics['cpu_temp'] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_cpu_temp/state"
        self.topics['cpu_usage'] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_avg_cpu/state"
        self.topics['memory_usage'] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_memory_usage/state"
        self.topics['uptime'] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_uptime/state"

        dev_discovery = {
            "dev": {
                "ids": self.device_id,
                "name": self.device_name,
                "sw_version": self.get_os_info(),
                "model": self.get_hardware_info(),
            },
            "o": {
                "name": "linux_mqtt_ha",
                "sw": "1.0",
                "url": "https://github.com/susembed/linux_mqtt_ha"
            },
            "cmps": {
                f"{self.device_id}_last_boot": {
                    "p": "sensor",
                    "name": "Last Boot",
                    "icon":"mdi:clock",
                    "state_topic": f"{self.topics['uptime']}",
                    "value_template":"{{now() - timedelta( seconds = (value |float(0)))}}",
                    "device_class":"timestamp",
                    "unique_id": f"{self.device_id}_last_boot",
                },
                f"{self.device_id}_cpu_temp": {
                    "p": "sensor",
                    "name": "CPU Temperature",
                    "unit_of_measurement": "°C",
                    "state_topic": f"{self.topics['cpu_temp']}",
                    "json_attributes_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_cpu_temp/state",
                    "json_attributes_template": "{{ value_json.attrs | tojson }}",
                    "value_template": "{{ value_json.temperature }}",
                    "device_class": "temperature",
                    "icon": "mdi:thermometer",
                    "unique_id": f"{self.device_id}_cpu_temp",
                    "state_class": "measurement",
                },
                f"{self.device_id}_cpu_usage": {
                    "p": "sensor",
                    "name": "CPU Usage",
                    "unit_of_measurement": "%",
                    "suggested_display_precision": 1,
                    "state_topic": f"{self.topics['cpu_usage']}",
                    "json_attributes_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_avg-cpu/state",
                    "json_attributes_template": "{{ value_json | tojson }}",
                    "value_template": "{{ 100 - (value_json.idle | float(0)) }}",
                    "icon": "mdi:cpu-64-bit",
                    "unique_id": f"{self.device_id}_cpu_usage",
                    "state_class": "measurement"
                },
                f"{self.device_id}_memory_usage": {
                    "p": "sensor",
                    "name": "Memory Usage",
                    "unit_of_measurement": "%",
                    "state_topic": f"{self.topics['memory_usage']}",
                    "json_attributes_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_memory_usage/state",
                    "json_attributes_template": "{{ value_json.mem | tojson }}",
                    "value_template": "{{ value_json.mem_usage }}",
                    "icon": "mdi:memory",
                    "unique_id": f"{self.device_id}_memory_usage",
                    "state_class": "measurement"
                },
                f"{self.device_id}_swap_usage": {
                    "p": "sensor",
                    "name": "Swap Usage",
                    "unit_of_measurement": "%",
                    "state_topic": f"{self.topics['memory_usage']}",
                    "json_attributes_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_swap_usage/state",
                    "json_attributes_template": "{{ value_json.swap | tojson }}",
                    "value_template": "{{ value_json.swap_usage }}",
                    "icon": "mdi:memory",
                    "unique_id": f"{self.device_id}_swap_usage",
                    "state_class": "measurement"
                },
            }
        }

        # Disk sensors
        self.topics['disk_smart'] = {}
        self.topics['disk_info'] = {}
        self.topics['disk_load'] = {}
        self.topics['disk_usage'] = {}
        
        # Get initial disk mapping
        disk_serials = self.get_disk_list_by_serial()
        
        for serial in disk_serials:
            # display_name = self.get_disk_display_name(serial)
            safe_serial = serial.replace('-', '_').replace(' ', '_')  # Make serial safe for MQTT topics
            #### Rewrite this part to device_discovery
            self.topics['disk_smart'][serial] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_disk_smart_{safe_serial}/state"
            self.topics['disk_info'][serial] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_disk_info_{safe_serial}/state"
            self.topics['disk_load'][serial] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_disk_load_{safe_serial}/state"
            self.topics['disk_usage'][serial] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_disk_usage_{safe_serial}/state"

            if self.disk_serial_mapping.get(serial) == self.root_disk:
                disk_name = "disk root"
            else:
                disk_name= f"disk {serial[:8]}"
            dev_discovery["cmps"][f"{self.device_id}_disk_smart_{safe_serial}"] = {
                "p": "binary_sensor",
                "name": f"{disk_name} SMART health",
                "state_topic": self.topics['disk_smart'][serial],
                "json_attributes_topic": self.topics['disk_smart'][serial],
                "json_attributes_template": "{{ value_json.attrs | tojson }}",
                "value_template": "{{'OFF' if value_json.smart_passed|int(0) == 1 else 'ON'}}",
                "device_class": "problem",
                "icon": "mdi:harddisk",
                "unique_id": f"{self.device_id}_disk_smart_{safe_serial}",
                # "state_class": "measurement"
            }
            dev_discovery["cmps"][f"{self.device_id}_disk_temp_{safe_serial}"] = {
                "p": "sensor",
                "name": f"{disk_name} temperature",
                "state_topic": self.topics['disk_smart'][serial],
                "value_template": "{{ value_json.temperature }}",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "icon": "mdi:thermometer",
                "unique_id": f"{self.device_id}_disk_temp_{safe_serial}",
                "state_class": "measurement"
            }
            dev_discovery["cmps"][f"{self.device_id}_disk_info_{safe_serial}"] = {
                "p": "sensor",
                "name": f"{disk_name} info",
                "state_topic": self.topics['disk_info'][serial],
                "json_attributes_topic": self.topics['disk_info'][serial],
                "json_attributes_template": "{{ value_json | tojson }}",
                "value_template": "{{ value_json.model_name }}",
                # "device_class": "diagnostic",
                "icon": "mdi:harddisk",
                "unique_id": f"{self.device_id}_disk_info_{safe_serial}",
                }
            dev_discovery["cmps"][f"{self.device_id}_disk_write_{safe_serial}"] = {
                "p": "sensor",
                "name": f"{disk_name} write speed",
                "state_topic": self.topics['disk_load'][serial],
                "value_template": "{{ value_json.write_kbs | float(0) }}",
                "unit_of_measurement": "kB/s",
                "device_class": "data_rate",
                "icon": "mdi:harddisk",
                "unique_id": f"{self.device_id}_disk_write_{safe_serial}",
                "state_class": "measurement"
            }
            dev_discovery["cmps"][f"{self.device_id}_disk_read_{safe_serial}"] = {
                "p": "sensor",
                "name": f"{disk_name} read speed",
                "state_topic": self.topics['disk_load'][serial],
                "value_template": "{{ value_json.read_kbs | float(0) }}",
                "unit_of_measurement": "kB/s",
                "device_class": "data_rate",
                "icon": "mdi:harddisk",
                "unique_id": f"{self.device_id}_disk_read_{safe_serial}",
                "state_class": "measurement"
            }
            dev_discovery["cmps"][f"{self.device_id}_disk_util_{safe_serial}"] = {
                "p": "sensor",
                "name": f"{disk_name} utilization",
                "state_topic": self.topics['disk_load'][serial],
                "value_template": "{{ value_json.util | float(0) }}",
                "unit_of_measurement": "%",
                "icon": "mdi:harddisk",
                "unique_id": f"{self.device_id}_disk_util_{safe_serial}",
                "state_class": "measurement"
            }
            dev_discovery["cmps"][f"{self.device_id}_disk_usage_{safe_serial}"] = {
                "p": "sensor",
                "name": f"{disk_name} usage",
                "state_topic": self.topics['disk_usage'][serial],
                "json_attributes_topic": self.topics['disk_usage'][serial],
                "json_attributes_template": "{{ value_json.attrs | tojson }}",
                "value_template": "{{ value_json.usage_percent }}",
                "unit_of_measurement": "%",
                "icon": "mdi:harddisk",
                "unique_id": f"{self.device_id}_disk_usage_{safe_serial}",
                "state_class": "measurement"
            }

        self.topics['net_stats'] = {}
        for if_name in self.ifs_name:
            safe_ifname = if_name.replace('-', '_').replace(' ', '_').replace('@', '_')  # Make interface name safe for MQTT topics
            self.topics['net_stats'][if_name] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_net_stats_{safe_ifname}/state"
            dev_discovery["cmps"][f"{self.device_id}_net_stats_{safe_ifname}_rx"] = {
                "p": "sensor",
                "name": f"{if_name} Rx speed",
                "state_topic": self.topics['net_stats'][if_name],
                "value_template": "{{ value_json.rx_speed | int(0)}}", 
                "unit_of_measurement": "B/s",
                "device_class": "data_rate",
                "icon": "mdi:download",
                "unique_id": f"{self.device_id}_net_stats_{safe_ifname}_rx",
                "state_class": "measurement"
                }
            dev_discovery["cmps"][f"{self.device_id}_net_stats_{safe_ifname}_tx"] = {
                "p": "sensor",
                "name": f"{if_name} Tx speed",
                "state_topic": self.topics['net_stats'][if_name],
                "value_template": "{{ value_json.tx_speed | int(0)}}", 
                "unit_of_measurement": "B/s",
                "device_class": "data_rate",
                "icon": "mdi:upload",
                "unique_id": f"{self.device_id}_net_stats_{safe_ifname}_tx",
                "state_class": "measurement"
            }
            dev_discovery["cmps"][f"{self.device_id}_net_stats_{safe_ifname}_link_speed"] = {
                "p": "sensor",
                "name": f"{if_name} Link Speed",
                "state_topic": self.topics['net_stats'][if_name],
                "value_template": "{{ value_json.link_speed | int(0)}}", 
                "unit_of_measurement": "Mbit/s",
                "suggested_display_precision": 0,
                "device_class": "data_rate",
                "icon": "mdi:speedometer",
                "unique_id": f"{self.device_id}_net_stats_{safe_ifname}_link_speed",
                "state_class": "measurement"
            }
            dev_discovery["cmps"][f"{self.device_id}_net_stats_{safe_ifname}_duplex"] = {
                "p": "sensor",
                "name": f"{if_name} Duplex",
                "state_topic": self.topics['net_stats'][if_name],
                "value_template": "{{ value_json.duplex }}",
                "icon": "mdi:network",
                "unique_id": f"{self.device_id}_net_stats_{safe_ifname}_duplex",
            }

        self.mqtt_publish(f"{self.ha_discovery_prefix}/device/{self.device_id}/config", json.dumps(dev_discovery), True)
    
    def publish_onetime_sensors(self):
        """Publish one-time sensors (uptime)"""
        print("Publishing one-time sensors...")
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.read().split()[0])
        # uptime_hours = self.get_uptime_hours()
        self.mqtt_publish(self.topics['uptime'], str(uptime_seconds), True)
    
    def publish_slow_sensors(self):
        """Publish slow interval sensors (SMART data)"""
        print("Publishing slow interval sensors (SMART data)...")
        
        # Update disk mapping and get current serials
        disk_serials = self.get_disk_list_by_serial()
        
        for serial in disk_serials:
            if serial in self.topics['disk_smart']:
                smart_data = self.get_disk_smart(serial)
                self.mqtt_publish(self.topics['disk_smart'][serial], json.dumps(smart_data))
    def publish_disk_info_and_status(self):
        """Publish disk info and status sensors"""
        print("Publishing disk info and status sensors...")
        
        # Update disk mapping and get current serials
        disk_serials = self.get_disk_list_by_serial()
        
        for serial in disk_serials:
            if serial in self.topics['disk_info']:
                disk_path = self.disk_serial_mapping.get(serial, "")
                if not disk_path:
                    continue
                
                disk_info = self.get_disk_info(disk_path)
                self.mqtt_publish(self.topics['disk_info'][serial], json.dumps(disk_info), True)
    def publish_network_sensors(self):
        """Publish network interface sensors"""
        # print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Collecting network interface data...")
        
        for ifname in self.ifs_name:
            if ifname not in self.if_statistics:
                self.if_statistics[ifname] = {"rx_bytes": 0, "tx_bytes": 0}
            try:
                # Get network interface statistics
                with open(f'/sys/class/net/{ifname}/statistics/rx_bytes', 'r') as f:
                    rx_delta = int(f.read().strip()) - self.if_statistics[ifname]["rx_bytes"]
                    if rx_delta < 0:
                        rx_speed = 0
                    else:
                        rx_speed = int(rx_delta / self.fast_interval)
                with open(f'/sys/class/net/{ifname}/statistics/tx_bytes', 'r') as f:
                    tx_delta = int(f.read().strip()) - self.if_statistics[ifname]["tx_bytes"]
                    if tx_delta < 0:
                        tx_speed = 0
                    else:
                        tx_speed = int(tx_delta / self.fast_interval)
                with open(f'/sys/class/net/{ifname}/speed', 'r') as f:
                    link_speed = int(f.read().strip()) 
                with open(f'/sys/class/net/{ifname}/duplex', 'r') as f:
                    duplex = f.read().strip()
                
                # Use the correct topic from discovery configuration
                payload = json.dumps({
                    "rx_speed": rx_speed,  # Bytes per second
                    "tx_speed": tx_speed,  # Bytes per second
                    "link_speed": link_speed,
                    "duplex": duplex,
                })
                # Use the correct topic instead of hardcoded one
                self.mqtt_publish(self.topics['net_stats'][ifname], payload)
                
            except FileNotFoundError:
                print(f"Network interface {ifname} not found, skipping.")
                continue
            except Exception as e:
                print(f"Error reading network stats for {ifname}: {e}")
                continue
    def publish_fast_sensors(self):
        """Publish fast interval sensors"""
        # print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Collecting iostat data ({self.fast_interval}s average)...")
        
        # Prepare network interface sensors before collecting iostat data
        for ifname  in self.ifs_name:
            try:
                if ifname not in self.if_statistics:
                    self.if_statistics[ifname] = {"rx_bytes": 0, "tx_bytes": 0}
                # Get network interface statistics
                with open(f'/sys/class/net/{ifname}/statistics/rx_bytes', 'r') as f:
                    self.if_statistics[ifname]["rx_bytes"] = int(f.read().strip())
                with open(f'/sys/class/net/{ifname}/statistics/tx_bytes', 'r') as f:
                    self.if_statistics[ifname]["tx_bytes"] = int(f.read().strip())
            except FileNotFoundError:
                print(f"Network interface {ifname} not found, skipping.")
                continue
        # Get all iostat data in one call (CPU + all disks)
        cpu_usage, disk_data = self.get_iostat_data()

        self.publish_network_sensors()
        
        # CPU metrics
        cpu_temp_data = self.get_cpu_temperature()
        self.mqtt_publish(self.topics['cpu_usage'], json.dumps(cpu_usage))
        self.mqtt_publish(self.topics['cpu_temp'], json.dumps(cpu_temp_data))
        
        # System metrics
        self.mqtt_publish(self.topics['memory_usage'], json.dumps( self.get_memory_usage()))
        
        # Disk metrics - update mapping first
        disk_serials = self.get_disk_list_by_serial()
        
        for serial in disk_serials:
            disk_path = self.disk_serial_mapping.get(serial, "")
            if not disk_path:
                continue
                
            disk_name = os.path.basename(disk_path)
            
            # Use disk I/O stats directly from iostat data
            if disk_name in disk_data:
                self.mqtt_publish(self.topics['disk_load'][serial], json.dumps(disk_data[disk_name]))
            if disk_path == self.root_disk:
                # For root disk, also publish disk usage
                self.mqtt_publish(self.topics['disk_usage'][serial], json.dumps(self.get_disk_usage(self.root_block)))
                # For normal disks, only fetch data of the first partition
            else:
                self.mqtt_publish(self.topics['disk_usage'][serial], json.dumps(self.get_disk_usage(f"{disk_path}1")))
    
    def cleanup(self, signum=None, frame=None):
        """Handle script termination"""
        print("Cleaning up...")
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        sys.exit(0)
    
    def run(self, dry_run: bool = False):
        """Main monitoring loop"""
        self.dry_run = dry_run
        
        print(f"Starting Linux System Monitor for {self.device_name}")
        print(f"MQTT Broker: {self.mqtt_broker}:{self.mqtt_port}")
        
        # Setup MQTT connection
        # self.setup_mqtt()

        # Get OS's root partition block device
        self.root_block = self.run_command(["findmnt", "-n", "-o", "SOURCE", "/"])
        self.root_disk = re.sub(r'\d+$', '', self.root_block)  # Remove partition number
        print(f"Root disk: {self.root_disk}")
        # Update disk mapping, this also update discovery
        self.update_disk_mapping()

        if self.dry_run:
            print("DRY RUN MODE: Will only print MQTT messages, not publish them")
        
        # Check dependencies
        if not self.check_dependencies():
            sys.exit(1)
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.cleanup)
        signal.signal(signal.SIGTERM, self.cleanup)
        
        
        # Setup Home Assistant discovery
        # self.setup_discovery()
        
        # Publish one-time sensors
        self.publish_onetime_sensors()
        
        print("########## Starting monitoring loop ##########")
        
        # Main monitoring loop
        while True:
            current_time = int(time.time())
            
            # Check if it's time for slow sensors update
            if current_time - self.last_slow_update >= self.slow_interval:
                self.publish_slow_sensors()
                self.last_slow_update = current_time
            
            # Publish fast sensors (includes built-in sleep via iostat)
            self.publish_fast_sensors()
            
            # print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Published sensor data (next update in {self.fast_interval}s)")
            # No need for sleep here as iostat already waits fast_interval seconds
    
    def update_disk_mapping(self) -> Dict[str, str]:
        """Update disk serial to device path mapping using lsblk JSON output"""
        output = self.run_command(["lsblk", "-d", "-o", "NAME,TRAN,SERIAL,SIZE,MODEL", "-J", "--tree"])
        
        if not output:
            return self.disk_serial_mapping
        
        try:
            data = json.loads(output)
            new_mapping = {}
            new_info_cache = {}
            
            for device in data.get("blockdevices", []):
                name = device.get("name", "")
                serial = device.get("serial", "")
                tran = device.get("tran", "")
                size = device.get("size", "")
                model = device.get("model", "")
                
                # Only include physical disks with serials
                if name and serial and tran in ["sata", "nvme", "usb", "scsi"]:
                    device_path = f"/dev/{name}"
                    # Verify device exists and matches our disk pattern
                    if os.path.exists(device_path) and re.match(r'^/dev/(sd|nvme|hd)', device_path):
                        new_mapping[serial] = device_path
                        new_info_cache[serial] = {
                            "name": name,
                            "model": model or "Unknown",
                            "size": size or "Unknown",
                            "transport": tran
                        }
            
            # Update class variables
            old_serials = set(self.disk_serial_mapping.keys())
            new_serials = set(new_mapping.keys())
            
            # Log changes
            added_serials = new_serials - old_serials
            removed_serials = old_serials - new_serials
            
            if added_serials:
                print(f"New disks detected: {', '.join(added_serials)}")
            if removed_serials:
                print(f"Disks removed: {', '.join(removed_serials)}")
            
            self.disk_serial_mapping = new_mapping
            self.disk_info_cache = new_info_cache
            print(f"Updated disk mapping: {len(self.disk_serial_mapping)} disks found")

            self.setup_discovery()  # Update discovery configuration
            # Add delay to allow Home Assistant to process discovery messages
            if not self.dry_run:
                print("Waiting for Home Assistant to process discovery messages...")
                time.sleep(5)  # 5 second delay
            
            self.publish_disk_info_and_status()
            print("Updated disk entities discovery and disk info and status values")
            for serial, path in self.disk_serial_mapping.items():
                print(f"  {serial}: {path} ({self.disk_info_cache.get(serial, {}).get('model', 'Unknown')})")
            return self.disk_serial_mapping
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Error parsing lsblk JSON output: {e}")
            return self.disk_serial_mapping

    def get_disk_list_by_serial(self) -> List[str]:
        """Get list of disk serials (updated each call)"""
        return list(self.disk_serial_mapping.keys())

    # def get_disk_display_name(self, serial: str) -> str:
    #     """Get a human-readable name for a disk based on its serial"""
    #     info = self.disk_info_cache.get(serial, {})
    #     name = info.get("name", serial[:8])
    #     model = info.get("model", "Unknown")
    #     size = info.get("size", "")
        
    #     if model != "Unknown" and size:
    #         return f"{name} ({model} {size})"
    #     elif model != "Unknown":
    #         return f"{name} ({model})"
    #     else:
    #         return f"{name} (S/N: {serial[:8]})"
def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Linux System Monitor with MQTT and Home Assistant Discovery")
    parser.add_argument("--dry-run", action="store_true", 
                       help="Print MQTT topics and messages without publishing")
    
    args = parser.parse_args()
    
    monitor = LinuxSystemMonitor()
    monitor.run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
