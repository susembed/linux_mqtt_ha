#!/usr/bin/env python3

"""
Linux System Monitoring Script with MQTT and Home Assistant Discovery
Monitors: CPU usage/temp, System load, Memory, Disk SMART, Disk I/O
Publishes to MQTT broker with Home Assistant autodiscovery
"""
# TODO:
# - Auto detect unsupported SMART before create sensors in discovery
# - Add docker container monitoring
# - Add option to set discovery to persistent and fixed disks list
import json
import time
import subprocess
import argparse
import signal
import sys
import os
import re
from typing import Dict, List, Tuple
import paho.mqtt.publish as publish
import ssl
# Load environment variables from .env file
try:
    import dotenv
except ImportError:
    print("Warning: python-dotenv not installed. Install with: pip install python-dotenv")
    sys.exit(1)
try:
    import requests_unixsocket
except ImportError:
    print("Warning: requests_unixsocket not installed. Install with: pip install requests-unixsocket")
    sys.exit(1)

class LinuxSystemMonitor:
    def __init__(self):
        # Helper function to safely get environment variables
        def get_env_var(key: str, default: str = "") -> str:
            """Safely get environment variable with fallback to default"""
            value = dotenv.get_key('.env', key)
            if value is not None:
                print(f"Loaded from .env: {key} = '{value}'")
                return value
            return default

        # Configuration - Load from environment variables with fallbacks
        self.mqtt_broker = get_env_var('MQTT_BROKER', 'localhost')
        self.mqtt_port = int(get_env_var('MQTT_PORT', '1883'))
        self.mqtt_user = get_env_var('MQTT_USER', '')
        self.mqtt_pass = get_env_var('MQTT_PASS', '')

        interfaces_str = get_env_var('NETWORK_INTERFACES', 'eth0')
        self.ifs_name = [iface.strip() for iface in interfaces_str.split(",") if iface.strip()]
        
        container_ids_str = get_env_var('CONTAINER_IDS', '')
        self.container_ids = [cid.strip() for cid in container_ids_str.split(",") if cid.strip()] if container_ids_str else []

        ignore_sensors_str = get_env_var('IGNORE_SENSORS', '')
        self.ignore_sensors = [sensor.strip() for sensor in ignore_sensors_str.split(",") if sensor.strip()] if ignore_sensors_str else []
        
        ignore_disks_smart_str = get_env_var('IGNORE_DISKS_FOR_SMART', '')
        self.ignore_disks_for_smart = [disk.strip() for disk in ignore_disks_smart_str.split(",") if disk.strip()] if ignore_disks_smart_str else []
        
        ignore_disks_temp_str = get_env_var('IGNORE_DISKS_FOR_TEMP', '')
        self.ignore_disks_for_temp = [disk.strip() for disk in ignore_disks_temp_str.split(",") if disk.strip()] if ignore_disks_temp_str else []
        
        ignore_disks_status_str = get_env_var('IGNORE_DISKS_FOR_STATUS', '')
        self.ignore_disks_for_status = [disk.strip() for disk in ignore_disks_status_str.split(",") if disk.strip()] if ignore_disks_status_str else []
        
        ignore_disks_usage_str = get_env_var('IGNORE_DISKS_FOR_USAGE', '')
        self.ignore_disks_for_usage = [disk.strip() for disk in ignore_disks_usage_str.split(",") if disk.strip()] if ignore_disks_usage_str else []

        ignore_disks_info_str = get_env_var('IGNORE_DISKS_FOR_INFO', '')
        self.ignore_disks_for_info = [disk.strip() for disk in ignore_disks_info_str.split(",") if disk.strip()] if ignore_disks_info_str else []
        
        self.fast_interval = int(get_env_var('FAST_INTERVAL', '10'))
        self.slow_interval = int(get_env_var('SLOW_INTERVAL', '30'))
        self.ha_discovery_prefix = get_env_var('HA_DISCOVERY_PREFIX', 'homeassistant')
        self.overwrite_device_id = get_env_var('OVERWRITE_DEVICE_ID', '')

        with open('/etc/hostname', 'r') as f:
            self.hostname = f.read().strip()
        self.mqtt_client_id = f"linux_mqtt_ha_on_{self.hostname}"
        if self.overwrite_device_id:
            self.device_id = self.overwrite_device_id.lower().replace(" ", "_")
            self.device_name = self.overwrite_device_id
        else:
            self.device_name = self.hostname
        
            self.device_id = self.hostname.lower().replace(" ", "_")
        # Dry run mode
        self.dry_run = False
        
        # Global variables
        self.script_start_time = int(time.time())
        self.last_slow_update = 0
        
        # Cache variables
        self.disk_serial_mapping = {}  # Maps serial -> device path
        self.block_to_serial = {}       # Maps block device -> serial
        self.disk_info_cache = {}      # Maps serial -> disk info (name, model, size)
        self.disk_bridge_type = {}  # Maps serial -> bridge type (e.g., "sat", )
        self.root_disk = None     # Root disk device name (e.g., "/dev/sda1")
        self.root_block = None         # Root block device name
        self.if_statistics = {}
        self.one_time_payload = {}
        self.fast_payload = {}
        self.slow_payload = {}
        self.disk_info_payload = {}
        self.cpu_core_count = None

        self.slow_topic = f"{self.ha_discovery_prefix}/linux_ha_mqtt_{self.device_id}/slow"  
        self.fast_topic = f"{self.ha_discovery_prefix}/linux_ha_mqtt_{self.device_id}/fast"  
        self.disk_info_topic = f"{self.ha_discovery_prefix}/linux_ha_mqtt_{self.device_id}/diskinfo"  
        self.one_time_topic = f"{self.ha_discovery_prefix}/linux_ha_mqtt_{self.device_id}/one_time"  
        # MQTT client
        self.auth = None
        self.tls = None
        
        # Topic storage
        self.topics = {}
        
    def check_dependencies(self) -> bool:
        """Check if required system tools are available"""
        # if self.dry_run:
        #     print("DRY RUN MODE: Skipping dependency checks")
        #     return True
            
        missing_deps = []
        required_commands = ["smartctl", "sensors", "iostat", "hdparm"]
        if "disk_smart" in self.ignore_sensors:
            required_commands.remove("smartctl")
        
        for cmd in required_commands:
            if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
                if cmd == "smartctl":
                    missing_deps.append("smartmontools")
                elif cmd == "sensors":
                    missing_deps.append("lm-sensors")
                elif cmd == "iostat":
                    missing_deps.append("sysstat")
                elif cmd == "hdparm":
                    missing_deps.append("hdparm")
        
        if missing_deps:
            print(f"Missing dependencies: {', '.join(missing_deps)}")
            print(f"Please install them with: sudo apt-get install {' '.join(missing_deps)}")
            return False
            
        return True
    
    def setup_mqtt(self):
        """Setup MQTT client connection"""
            
        if self.mqtt_user and self.mqtt_pass:
            self.auth = {'username': self.mqtt_user, 'password': self.mqtt_pass}
        
        if self.mqtt_port == 8883:
            self.tls = {'cert_reqs': ssl.CERT_REQUIRED, 'tls_version': ssl.PROTOCOL_TLS}

    def mqtt_publish(self, topic: str, payload: str, retain: bool = False):
        """Publish MQTT message"""
        if self.dry_run:
            retain_flag = " [RETAINED]" if retain else ""
            print(f"[DRY RUN]{retain_flag} Topic: {topic}")
            print(f"[DRY RUN]{retain_flag} Payload: {payload}")
            print("---")
            return
        
        try:
            publish.single(
                topic=topic,
                payload=payload,
                hostname=self.mqtt_broker,
                port=self.mqtt_port,
                auth=self.auth,
                tls=self.tls,
                client_id=self.mqtt_client_id,
                retain=retain,
                keepalive=60
            )
        except Exception as e:
            print(f"Failed to send MQTT message: {e}")
    def run_command(self, cmd: List[str], timeout: int = 30) -> str:
        """Run system command and return output"""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return ""
    def run_command_accept_error(self, cmd: List[str], timeout: int = 30) -> str:
        """Run system command and return output"""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.stdout.strip():
                return result.stdout.strip()
            elif result.stderr.strip():
                return result.stderr.strip()
            else:
                return ""
        except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            print(f"Command exception: {e}")
            return ""
    
    def update_iostat_data(self):
        """Get iostat data for CPU and disk metrics using JSON output"""
        cmd = ["iostat", "-d", str(self.fast_interval), "1", "-y", "-c", "-x", "-o", "JSON"]
        output = self.run_command(cmd)
        
        if not output:
            return
        
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
            
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            print(f"Error parsing iostat JSON output: {e}")
            return

        self.fast_payload['cpu_avg'] = cpu_data
        for serial, disk_path in self.disk_serial_mapping.items():
            if not disk_path:
                continue
                
            disk_name = os.path.basename(disk_path)
            
            # Use disk I/O stats directly from iostat data
            if disk_name in disk_data:
                self.fast_payload[f"disk_io_{serial}"] = disk_data[disk_name]
    
    def update_cpu_temperature(self):
        """Get CPU temperature using JSON output from sensors"""
        # Try sensors command with JSON output first
        output = self.run_command(["sensors", "-j"])
        if output:
            try:
                data = json.loads(output)
                # Look for coretemp, cpu thermal, or similar CPU temperature sensor
                for sensor_name, sensor_data in data.items():
                    if ("coretemp" in sensor_name.lower() or 
                        "cpu_thermal" in sensor_name.lower()):
                        
                        # Handle virtual thermal sensors (e.g., cpu_thermal-virtual-0)
                        if "thermal" in sensor_name.lower() and "virtual" in sensor_name.lower():
                            # Look for temp1 data
                            if "temp1" in sensor_data and isinstance(sensor_data["temp1"], dict):
                                temp_value = sensor_data["temp1"].get("temp1_input", 0.0)
                                self.fast_payload['cpu_temp'] = {
                                    "temperature": round(temp_value, 1),
                                    "attrs": {
                                        "sensor": sensor_name,
                                        "source": "virtual_thermal"
                                    }
                                }
                                return
                    
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
                        
                        self.fast_payload['cpu_temp'] = {
                            "temperature": round(main_temp, 1),
                            "attrs": {
                                "sensor": sensor_name,
                                "cores": core_temps_array  # Array of core temperatures
                            }
                        }
                        return

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"Error parsing sensors JSON output: {e}")
        
        # Fallback to thermal zone
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000.0
                self.fast_payload['cpu_temp'] = {
                    "temperature": round(temp, 1),
                    "attrs": {
                        "sensor": "thermal_zone0",
                        "source": "sysfs"
                    }
                }
        except (FileNotFoundError, ValueError):
            self.fast_payload['cpu_temp'] = {
                "temperature": 0.0,
                "attrs": {
                    "sensor": "unknown",
                    "error": "No temperature sensors found"
                }
            }
    def update_cpu_freq(self) -> None:
        cores_freq = []
        for core in range(0, self.cpu_core_count): 
            with open(f'/sys/devices/system/cpu/cpu{core}/cpufreq/scaling_cur_freq', 'r') as f:
                try:
                    freq = int(f.read().strip()) / 1000.0  # Convert to MHz
                    cores_freq.append(int(freq))
                except ValueError:
                    cores_freq.append(0.0)
        if cores_freq:
            self.fast_payload['cpu_freq'] = {
                "avg_freq": int(sum(cores_freq) / len(cores_freq)),
                "attrs": {
                    "cores": cores_freq,
                    "count": len(cores_freq)
                }
            }
        else:
            self.fast_payload['cpu_freq'] = {
                "avg_freq": 0,
                "attrs": {
                    "cores": [],
                    "count": 0
                }
            }

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
    def update_container_stats(self) :
        """Get Docker container stats using requests_unixsocket"""
        try:
            session = requests_unixsocket.Session()
            for container_id in self.container_ids:
                response = session.get(f'http+unix://%2Fvar%2Frun%2Fdocker.sock/containers/{container_id}/stats?stream=true')
                containers = response.json()

                for container in containers:
                    container_id = container['Id']
                    container_name = container['Names'][0].lstrip('/')
                
                # Get stats for the container
                stats_response = session.get(f'http+unix://%2Fvar%2Frun%2Fdocker.sock/containers/{container_id}/stats?stream=false')
                stats = stats_response.json()
                
                # Extract CPU and memory usage
                cpu_usage = stats['cpu_stats']['cpu_usage']['total_usage']
                mem_usage = stats['memory_stats']['usage']
                
                self.fast_payload[f"container_{container_name}_cpu"] = {
                    "usage": cpu_usage,
                    "attrs": {
                        "container_id": container_id,
                        "container_name": container_name
                    }
                }
                
                self.fast_payload[f"container_{container_name}_mem"] = {
                    "usage": mem_usage,
                    "attrs": {
                        "container_id": container_id,
                        "container_name": container_name
                    }
                }
                
        except requests_unixsocket.exceptions.ConnectionError as e:
            print(f"Error connecting to Docker socket: {e}")

    def update_disk_status(self) -> Dict[str, Dict]:
        """Get disk power status for multiple disks using hdparm in batch"""
        disk_paths = self.disk_path_mapping.keys()
        if not disk_paths:
            return

        output = self.run_command(["hdparm", "-C"] + list(disk_paths))
        if not output:
            return
        current_device = None
        for line in output.split('\n'):
            line = line.strip()
            
            # Check if this line contains a device path
            if line.endswith(':') and any(line.startswith(path) for path in disk_paths):
                current_device = line.rstrip(':')
            elif "drive state is:" in line and current_device:
                # Extract status after "drive state is:"
                status = line.split("drive state is:")[1].strip()
                self.fast_payload[f"disk_status_{self.disk_path_mapping.get(current_device, 'unknown')}"] = {"status": status}

                current_device = None
        return

    def update_disk_usage(self) :
        """Get disk usage statistics using lsblk with JSON output (accepts device path)"""
        cmd = ["lsblk", "-o", "NAME,SIZE,FSUSED,FSAVAIL,FSTYPE,FSSIZE,MOUNTPOINT", "-J", "-b"]
        cmd.extend(self.block_to_serial.keys())  # Add block devices to check
        output = self.run_command(cmd)

        try:
            data = json.loads(output)
            blockdevices = data.get("blockdevices", [])
            
            for block in blockdevices:
                def safe_int(value):
                    try:
                        return int(value) if value and value != "null" else 0
                    except (ValueError, TypeError):
                        return 0

                fssize = safe_int(block.get("fssize", "0"))
                fsused = safe_int(block.get("fsused", "0"))
                fsavail = safe_int(block.get("fsavail", "0"))
                usage_percent = float(round(fsused/(fsused+fsavail)*100 , 2)) if fsused > 0 or fsavail > 0  else 0.0

                serial = self.block_to_serial.get(f"/dev/{block.get('name', 'unknown')}", "unknown")
                self.fast_payload[f"disk_usage_{serial}"] = {
                    "usage_percent": usage_percent,
                    "attrs": {
                        "mount_point": block.get("mountpoint", "unmounted"),
                        "total": fssize,
                        "used": fsused,
                        "free": fsavail,
                        "fstype": block.get("fstype", "unknown") or "unknown",
                        "device_name": block.get("name", "unknown"),
                        "total_size": block.get("size", "unknown")
                    }
                }
                
        except (json.JSONDecodeError, ValueError, IndexError, KeyError) as e:
            print(f"Error parsing lsblk JSON output: {e}")

    def get_disk_smart(self, disk_path: str) -> Dict:
        """Get disk SMART data using JSON output from smartctl (accepts device path or serial)"""
        
        # Use smartctl with JSON output for comprehensive SMART data
        cmd = ["smartctl", "-A", "-H", "-j", disk_path]
        if self.disk_bridge_type.get(disk_path):
            cmd= ["smartctl", "-A", "-H", "-j", "-d", self.disk_bridge_type[disk_path], disk_path]
        output = self.run_command_accept_error(cmd)
        if not output:
            return {
                "smart_passed": 0,
                "temperature": 0,
                "attrs": {
                    "error": "No smartctl output"
                }
            }
        
        try:
            data = json.loads(output)
            
            # Check for smartctl exit status and messages
            smartctl_info = data.get("smartctl", {})
            exit_status = smartctl_info.get("exit_status", 0)
            messages = smartctl_info.get("messages", [])
            
            # Check for known error conditions
            error_conditions = []
            for message in messages:
                msg_string = message.get("string", "")
                if "Unknown USB bridge" in msg_string:
                    # The disk_info should handle this case, so it must be run first
                    error_conditions.append("USB bridge not supported")
                    break
                elif "SMART support is:" in msg_string and "Unavailable" in msg_string:
                    error_conditions.append("SMART not supported")
                elif message.get("severity") == "error":
                    error_conditions.append(msg_string)
            
            # If there are critical errors, return limited data
            if exit_status != 0 and error_conditions:
                return {
                    "smart_passed": 0,
                    "temperature": 0,
                    "attrs": {
                        "error": "; ".join(error_conditions),
                        "exit_status": exit_status,
                        "supported": False
                    }
                }

            # Extract SMART attributes
            smart_attrs = {
                "smart_passed": 1 if data.get("smart_status", {}).get("passed", False) else 0,
                "temperature": data.get("temperature", {}).get("current", 0),
                "attrs": {}
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
                smart_attrs["attrs"][key] = attr_value
            
            # Add support status to attributes
            smart_attrs["attrs"]["supported"] = True
            # if messages:
            #     smart_attrs["attrs"]["messages"] = [msg.get("string", "") for msg in messages]
            
            return smart_attrs
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Error parsing smartctl JSON output for {disk_path}: {e}")
            
            return {
                "smart_passed": 0,
                "temperature": 0,
                "attrs": {
                    "error": f"JSON parse error: {str(e)}",
                    "supported": False
                }
            }

    def get_disk_info(self, disk_path: str) -> Dict:
        """Get disk info data using JSON output from smartctl (accepts device path)"""

        # Use smartctl with JSON output for comprehensive SMART data
        if self.disk_bridge_type.get(disk_path):
            cmd= ["smartctl", "-i", "-j", "-d", self.disk_bridge_type[disk_path], disk_path]
        else:
            cmd = ["smartctl", "-i", "-j", disk_path]
        output = self.run_command_accept_error(cmd)
        if not output:
            return {}
        
        try:
            data = json.loads(output)
            
            # Check for smartctl exit status and messages
            smartctl_info = data.get("smartctl", {})
            exit_status = smartctl_info.get("exit_status", 0)
            messages = smartctl_info.get("messages", [])
            
            for message in messages:
                msg_string = message.get("string", "")
                if "Unknown USB bridge" in msg_string:
                    output = self.run_command_accept_error(["smartctl", "-i", "-d", "sat", "-j", disk_path])
                    if output:
                        data = json.loads(output)
                        exit_status = data.get("smartctl", {}).get("exit_status", 0)
                        if exit_status == 0:
                            self.disk_bridge_type[disk_path] = "sat"
                            print(f"Updated USB bridge type for {disk_path}: sat")
                            break
                        # If we got here, it means the USB bridge is supported
                    break
            
            # If there are critical errors, return limited data
            if exit_status != 0 :
                return {
                    "model_name": "Unknown",
                }
            # Extract device info
            info = {
                "device_name": data.get("device", {}).get("name", disk_path),
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
            if self.disk_bridge_type.get(disk_path):
                info["bridge_type"] = self.disk_bridge_type[disk_path]
            
            return info
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Error parsing smartctl JSON output for {disk_path}: {e}")
            
            return {
                "device_name": disk_path,
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
        """Get CPU information from /proc/cpuinfo"""
        """Update CPU frequency information"""
        if self.cpu_core_count is None:
            for i in range(0, 32):  # Check up to 32 cores
                cpu_path = f'/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq'
                if (not os.path.exists(cpu_path) or i==32):
                    self.cpu_core_count = i
                    print(f"Detected {self.cpu_core_count} CPU core(s)")
                    break
        
        cpu_name = "Unknown CPU"
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
        dev_discovery = {
            "dev": {
                "ids": self.device_id,
                "name": self.device_name,
                "sw_version": self.get_os_info(),
                "model": self.get_hardware_info(),
            },
            "o": {
                "name": "linux_mqtt_ha",
                "sw": "2.1",
                "url": "https://github.com/susembed/linux_mqtt_ha"
            },
            "cmps": {}
        }
        if "monitoring" not in self.ignore_sensors:
            dev_discovery["cmps"][f"{self.device_id}_monitoring"] = {
                "p": "binary_sensor",
                "name": "monitoring status",
                "state_topic": self.fast_topic,
                "json_attributes_topic": self.fast_topic,
                "json_attributes_template": "{{ value_json.script | tojson }}",
                "value_template": "ON",
                "device_class": "running",
                "off_delay": self.fast_interval * 2,
                "icon": "mdi:monitor-dashboard",
                "unique_id": f"{self.device_id}_monitoring",
            }
        if "last_boot" not in self.ignore_sensors:
            self.topics['uptime'] = f"{self.ha_discovery_prefix}/sensor/{self.device_id}_uptime/state"
            dev_discovery["cmps"][f"{self.device_id}_last_boot"] = {
                "p": "sensor",
                "name": "Last boot",
                "icon":"mdi:clock",
                "state_topic": self.one_time_topic,
                "value_template":"{{ (now() | as_timestamp - (value_json.uptime |float(0))) |round(0) | as_datetime |as_local}}",
                "device_class":"timestamp",
                "unique_id": f"{self.device_id}_last_boot",
            }
        if "cpu_usage" not in self.ignore_sensors:
            dev_discovery["cmps"][f"{self.device_id}_cpu_usage"] = {
                "p": "sensor",
                "name": "CPU usage",
                "unit_of_measurement": "%",
                "suggested_display_precision": 1,
                "state_topic": self.fast_topic,
                "json_attributes_topic": self.fast_topic,
                "json_attributes_template": "{{ value_json.cpu_avg | tojson }}",
                "value_template": "{{ 100 - (value_json.cpu_avg.idle | float(0)) }}",
                "icon": "mdi:cpu-64-bit",
                "unique_id": f"{self.device_id}_cpu_usage",
                "state_class": "measurement"
            }
        if "cpu_freq" not in self.ignore_sensors:
            dev_discovery["cmps"][f"{self.device_id}_cpu_freq"] = {
                "p": "sensor",
                "name": "CPU frequency",
                "unit_of_measurement": "MHz",
                "suggested_display_precision": 0,
                "state_topic": self.fast_topic,
                "json_attributes_topic": self.fast_topic,
                "json_attributes_template": "{{ value_json.cpu_freq.attrs | tojson }}",
                "value_template": "{{ value_json.cpu_freq.avg_freq | float(0) }}",
                "icon": "mdi:cpu-64-bit",
                "unique_id": f"{self.device_id}_cpu_freq",
                "state_class": "measurement"
            }
        if "cpu_temp" not in self.ignore_sensors:
            dev_discovery["cmps"][f"{self.device_id}_cpu_temp"] = {
                "p": "sensor",
                "name": "CPU temperature",
                "unit_of_measurement": "°C",
                "state_topic": self.fast_topic,
                "json_attributes_topic": self.fast_topic,
                "json_attributes_template": "{{ value_json.cpu_temp.attrs | tojson }}",
                "value_template": "{{ value_json.cpu_temp.temperature | float(0) }}",
                "device_class": "temperature",
                "icon": "mdi:thermometer",
                "unique_id": f"{self.device_id}_cpu_temp",
                "state_class": "measurement",
            }
        if "mem_usage" not in self.ignore_sensors:
            if "ram_usage" not in self.ignore_sensors:
                dev_discovery["cmps"][f"{self.device_id}_memory_usage"] = {
                    "p": "sensor",
                    "name": "Memory usage",
                    "unit_of_measurement": "%",
                    "state_topic": self.fast_topic,
                    "json_attributes_topic": self.fast_topic,
                    "json_attributes_template": "{{ value_json.mem_usage.mem | tojson }}",
                    "value_template": "{{ value_json.mem_usage.mem_usage | float(0) }}",
                    "icon": "mdi:memory",
                    "unique_id": f"{self.device_id}_memory_usage",
                    "state_class": "measurement"
                }
            if "swap_usage" not in self.ignore_sensors:
                dev_discovery["cmps"][f"{self.device_id}_swap_usage"] = {
                    "p": "sensor",
                    "name": "Swap usage",
                    "unit_of_measurement": "%",
                    "state_topic": self.fast_topic,
                    "json_attributes_topic": self.fast_topic,
                    "json_attributes_template": "{{ value_json.mem_usage.swap | tojson }}",
                    "value_template": "{{ value_json.mem_usage.swap_usage | float(0) }}",
                    "icon": "mdi:memory",
                    "unique_id": f"{self.device_id}_swap_usage",
                    "state_class": "measurement"
            }
        # Disk sensors
        
        for serial in self.disk_serial_mapping:
            # display_name = self.get_disk_display_name(serial)
            safe_serial = serial.replace('-', '_').replace(' ', '_')  # Make serial safe for MQTT topics
            #### Rewrite this part to device_discovery

            if self.disk_serial_mapping.get(serial) == self.root_disk:
                disk_name = "disk root"
            else:
                disk_name= f"disk {serial[:8]}"
            
            if serial not in self.ignore_disks_for_smart:
                if "disk_smart" not in self.ignore_sensors:
                    dev_discovery["cmps"][f"{self.device_id}_disk_smart_{safe_serial}"] = {
                        "p": "binary_sensor",
                        "name": f"{disk_name} SMART health",
                        "state_topic": self.slow_topic,
                        "json_attributes_topic": self.slow_topic,
                        "json_attributes_template": f"{{{{ value_json.disk_smart_{serial}.attrs | tojson }}}}",
                        "value_template": f"{{{{'OFF' if value_json.disk_smart_{serial}.smart_passed|int(0) == 1 else 'ON'}}}}",
                        "device_class": "problem",
                        "icon": "mdi:harddisk",
                        "unique_id": f"{self.device_id}_disk_smart_{safe_serial}",
                        # "state_class": "measurement"
                    }
                    dev_discovery["cmps"][f"{self.device_id}_disk_temp_{safe_serial}"] = {
                        "p": "sensor",
                        "name": f"{disk_name} temperature",
                        "state_topic": self.slow_topic,
                        "value_template": f"{{{{ value_json.disk_smart_{serial}.temperature }}}}",
                        "unit_of_measurement": "°C",
                        "device_class": "temperature",
                        "icon": "mdi:thermometer",
                        "unique_id": f"{self.device_id}_disk_temp_{safe_serial}",
                        "state_class": "measurement"
                    }
                if "disk_info" not in self.ignore_sensors:
                    dev_discovery["cmps"][f"{self.device_id}_disk_info_{safe_serial}"] = {
                        "p": "sensor",
                        "name": f"{disk_name} info",
                        "state_topic": self.disk_info_topic,
                        "json_attributes_topic": self.disk_info_topic,
                        "json_attributes_template": f"{{{{ value_json['{serial}'] | tojson }}}}",
                        "value_template": f"{{{{ value_json['{serial}'].model_name }}}}",
                        # "device_class": "diagnostic",
                        "icon": "mdi:harddisk",
                        "unique_id": f"{self.device_id}_disk_info_{safe_serial}",
                        }
            if "disk_load" not in self.ignore_sensors:
                dev_discovery["cmps"][f"{self.device_id}_disk_write_{safe_serial}"] = {
                    "p": "sensor",
                    "name": f"{disk_name} write speed",
                    "state_topic": self.fast_topic,
                    "value_template": f"{{{{ value_json.disk_io_{serial}.write_kbs | float(0) }}}}",
                    "unit_of_measurement": "kB/s",
                    "device_class": "data_rate",
                    "icon": "mdi:harddisk",
                    "unique_id": f"{self.device_id}_disk_write_{safe_serial}",
                    "state_class": "measurement"
                }
                dev_discovery["cmps"][f"{self.device_id}_disk_read_{safe_serial}"] = {
                    "p": "sensor",
                    "name": f"{disk_name} read speed",
                    "state_topic": self.fast_topic,
                    "value_template": f"{{{{ value_json.disk_io_{serial}.read_kbs | float(0) }}}}",
                    "unit_of_measurement": "kB/s",
                    "device_class": "data_rate",
                    "icon": "mdi:harddisk",
                    "unique_id": f"{self.device_id}_disk_read_{safe_serial}",
                    "state_class": "measurement"
                }
                dev_discovery["cmps"][f"{self.device_id}_disk_util_{safe_serial}"] = {
                    "p": "sensor",
                    "name": f"{disk_name} utilization",
                    "state_topic": self.fast_topic,
                    "value_template": f"{{{{ value_json.disk_io_{serial}.util | float(0) }}}}",
                    "unit_of_measurement": "%",
                    "icon": "mdi:harddisk",
                    "unique_id": f"{self.device_id}_disk_util_{safe_serial}",
                    "state_class": "measurement"
                }
            if "disk_usage" not in self.ignore_sensors:
                dev_discovery["cmps"][f"{self.device_id}_disk_usage_{safe_serial}"] = {
                    "p": "sensor",
                    "name": f"{disk_name} usage",
                    "state_topic": self.fast_topic,
                    "json_attributes_topic": self.fast_topic,
                    "json_attributes_template": f"{{{{ value_json.disk_usage_{serial}.attrs | tojson }}}}",
                    "value_template": f"{{{{ value_json.disk_usage_{serial}.usage_percent }}}}",
                    "unit_of_measurement": "%",
                    "icon": "mdi:harddisk",
                    "unique_id": f"{self.device_id}_disk_usage_{safe_serial}",
                    "state_class": "measurement"
                }
            if "disk_status" not in self.ignore_sensors:
                dev_discovery["cmps"][f"{self.device_id}_disk_status_{safe_serial}"] = {
                    "p": "sensor",
                    "name": f"{disk_name} status",
                    "state_topic": self.fast_topic,
                    "value_template": f"{{{{ value_json.disk_status_{serial}.status }}}}",
                    "icon": "mdi:power",
                    "unique_id": f"{self.device_id}_disk_status_{safe_serial}",
                }
        if "net_stats" not in self.ignore_sensors:
            self.topics['net_stats'] = {}
            for if_name in self.ifs_name:
                safe_ifname = if_name.replace('-', '_').replace(' ', '_').replace('@', '_')  # Make interface name safe for MQTT topics
                if "net_rx" not in self.ignore_sensors :
                    dev_discovery["cmps"][f"{self.device_id}_net_stats_{safe_ifname}_rx"] = {
                        "p": "sensor",
                        "name": f"{if_name} Rx speed",
                        "state_topic": self.fast_topic,
                        "value_template": f"{{{{ value_json.net_stats_{safe_ifname}.rx_speed | int(0) }}}}", 
                        "unit_of_measurement": "B/s",
                        "device_class": "data_rate",
                        "icon": "mdi:download",
                        "unique_id": f"{self.device_id}_net_stats_{safe_ifname}_rx",
                        "state_class": "measurement"
                        }
                if "net_tx" not in self.ignore_sensors:
                    dev_discovery["cmps"][f"{self.device_id}_net_stats_{safe_ifname}_tx"] = {
                        "p": "sensor",
                        "name": f"{if_name} Tx speed",
                        "state_topic": self.fast_topic,
                        "value_template": f"{{{{ value_json.net_stats_{safe_ifname}.tx_speed | int(0) }}}}", 
                        "unit_of_measurement": "B/s",
                        "device_class": "data_rate",
                        "icon": "mdi:upload",
                        "unique_id": f"{self.device_id}_net_stats_{safe_ifname}_tx",
                        "state_class": "measurement"
                    }
                if "net_link_speed" not in self.ignore_sensors:
                    dev_discovery["cmps"][f"{self.device_id}_net_stats_{safe_ifname}_link_speed"] = {
                        "p": "sensor",
                        "name": f"{if_name} link speed",
                        "state_topic": self.fast_topic,
                        "value_template": f"{{{{ value_json.net_stats_{safe_ifname}.link_speed | int(0) }}}}", 
                        "unit_of_measurement": "Mbit/s",
                        "suggested_display_precision": 0,
                        "device_class": "data_rate",
                        "icon": "mdi:speedometer",
                        "unique_id": f"{self.device_id}_net_stats_{safe_ifname}_link_speed",
                        "state_class": "measurement"
                    }
                if "net_duplex" not in self.ignore_sensors:
                    dev_discovery["cmps"][f"{self.device_id}_net_stats_{safe_ifname}_duplex"] = {
                        "p": "sensor",
                        "name": f"{if_name} duplex",
                        "state_topic": self.fast_topic,
                        "value_template": f"{{{{ value_json.net_stats_{safe_ifname}.duplex }}}}",
                        "icon": "mdi:network",
                        "unique_id": f"{self.device_id}_net_stats_{safe_ifname}_duplex",
                    }

        self.mqtt_publish(f"{self.ha_discovery_prefix}/device/{self.device_id}/config", json.dumps(dev_discovery), True)
    
    def publish_onetime_sensors(self):
        """Publish one-time sensors (uptime)"""
        print("Publishing one-time sensors...")
        if "last_boot" not in self.ignore_sensors:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.read().split()[0])
            self.one_time_payload["uptime"] = uptime_seconds
        self.mqtt_publish(self.one_time_topic, json.dumps(self.one_time_payload), True)
    def publish_disk_info(self):
        """Publish disk info and status sensors"""
        print("Publishing disk info and status sensors...")
        disk_info_payload = {}

        for serial, disk_path in self.disk_serial_mapping.items():
            disk_info_payload[f"{serial}"] = self.get_disk_info(disk_path)
        self.mqtt_publish(self.disk_info_topic, json.dumps(disk_info_payload), True)
    def update_network_sensors(self):
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
                safe_ifname = ifname.replace(".", "_")
                self.fast_payload[f"net_stats_{safe_ifname}"] = {
                    "rx_speed": rx_speed,  # Bytes per second
                    "tx_speed": tx_speed,  # Bytes per second
                    "link_speed": link_speed,
                    "duplex": duplex,
                }
                # Use the correct topic instead of hardcoded one
                
            except FileNotFoundError:
                print(f"Network interface {ifname} not found, skipping.")
                continue
            except Exception as e:
                print(f"Error reading network stats for {ifname}: {e}")
                continue

    def publish_slow_sensors(self):
        """Publish slow interval sensors (SMART data)"""
        # print("Publishing slow interval sensors (SMART data)...")

        for serial, disk_path in self.disk_serial_mapping.items():
            if serial not in self.ignore_disks_for_smart:
                if ("disk_smart" not in self.ignore_sensors):
                    self.slow_payload[f"disk_smart_{serial}"] = self.get_disk_smart(disk_path)
        self.mqtt_publish(self.slow_topic, json.dumps(self.slow_payload))
    def publish_fast_sensors(self):
        """Publish fast interval sensors"""
        # print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Collecting iostat data ({self.fast_interval}s average)...")
        
        if "cpu_temp" not in self.ignore_sensors:
            self.update_cpu_temperature()
        
        # System metrics
        if "mem_usage" not in self.ignore_sensors:
            self.fast_payload["mem_usage"] = self.get_memory_usage()
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
        self.update_iostat_data()
        self.update_cpu_freq()
        self.update_network_sensors()
        self.update_disk_usage()  # Update disk usage statistics
        self.update_disk_status()
        # for serial, disk_path in self.disk_serial_mapping.items():
        #     if serial not in self.ignore_disks_for_info:
        #         if  ("disk_status" not in self.ignore_sensors):
        #             self.fast_payload[f"disk_status_{serial}"] = self.get_disk_status(disk_path)
        # Publish fast sensors payload
        self.mqtt_publish(self.fast_topic, json.dumps(self.fast_payload))
    def cleanup(self, signum=None, frame=None):
        """Handle script termination"""
        print("Cleaning up...")

        sys.exit(0)
    
    def run(self, dry_run: bool = False):
        """Main monitoring loop"""
        last_execution = float(time.time())
        self.dry_run = dry_run
        
        print(f"Starting Linux System Monitor for {self.device_name}")
        print(f"MQTT Broker: {self.mqtt_broker}:{self.mqtt_port}")
        
        # Setup MQTT connection
        self.setup_mqtt()
        # Get OS's root partition block device
        self.root_block = self.run_command(["findmnt", "-n", "-o", "SOURCE", "/"])
        # Remove partition number - handle different storage device naming conventions# sda1 -> sda, mmcblk0p1 -> mmcblk0
        self.root_disk = re.sub(r'(p\d+|\d+)$', '', self.root_block)
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
        
        # Publish one-time sensors
        self.publish_onetime_sensors()
        
        print("########## Starting monitoring loop ##########")
        
        # Main monitoring loop
        while True:
            self.fast_payload["script"] = {
                "last_cycle_execution_time": (float(time.time()) - last_execution if last_execution else 0),
                "interval": self.fast_interval,
            }
            current_time = int(time.time())
            last_execution = float(time.time())
            # Check if it's time for slow sensors update
            if current_time - self.last_slow_update >= self.slow_interval:
                self.publish_slow_sensors()
                self.last_slow_update = current_time
            # Publish fast sensors (includes built-in sleep via iostat)
            self.publish_fast_sensors()
            self.update_disk_mapping()  # Update disk mapping if needed
    def update_disk_mapping(self):
        """Update disk serial to device path mapping using lsblk JSON output"""
        output = self.run_command(["lsblk", "-d", "-o", "NAME,TRAN,SERIAL,SIZE,MODEL", "-J", "--tree"])
        
        if not output:
            return self.disk_serial_mapping
        
        self.block_to_serial = {}
        try:
            data = json.loads(output)
            new_mapping = {}
            new_info_cache = {}
            self.disk_path_mapping = {}
            
            for device in data.get("blockdevices", []):
                name = device.get("name", "")
                serial = device.get("serial", "")
                tran = device.get("tran", "")
                size = device.get("size", "")
                model = device.get("model", "")
                
                # Only include physical disks with serials
                # if name and serial and tran in ["sata", "nvme", "usb", "scsi"]:
                disk_path = f"/dev/{name}"
                # Verify device exists and matches our disk pattern
                if os.path.exists(disk_path) and re.match(r'^/dev/(sd|nvme|hd|mmc)', disk_path):
                    new_mapping[serial] = disk_path
                    new_info_cache[serial] = {
                        "name": name,
                        "model": model or "Unknown",
                        "size": size or "Unknown",
                        "transport": tran
                    }
                    self.disk_path_mapping[disk_path] = serial
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
            if added_serials or removed_serials:
                self.disk_serial_mapping = new_mapping
                self.disk_info_cache = new_info_cache
                print(f"Updated disk mapping: {len(self.disk_serial_mapping)} disks found")
                for serial, disk_path in self.disk_serial_mapping.items():
                    if disk_path == self.root_disk:
                        block_path = self.root_block
                    else:
                        block_path = f"{disk_path}p1" if re.match(r'^/dev/(nvme|mmc)', disk_path) else f"{disk_path}1"
                    self.block_to_serial[block_path] = serial

                self.setup_discovery()  # Update discovery configuration
                # Add delay to allow Home Assistant to process discovery messages
                if not self.dry_run:
                    print("Waiting for Home Assistant to process discovery messages...")
                    time.sleep(5)  # 5 second delay
                
                self.publish_disk_info()
                print("Updated disk entities discovery and disk info and status values")
                for serial, path in self.disk_serial_mapping.items():
                    print(f"  {serial}: {path} ({self.disk_info_cache.get(serial, {}).get('model', 'Unknown')})")
            return
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Error parsing lsblk JSON output: {e}")
            return self.disk_serial_mapping

    def get_disk_list_by_serial(self) -> List[str]:
        """Get list of disk serials (updated each call)"""
        return list(self.disk_serial_mapping.keys())
    
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
