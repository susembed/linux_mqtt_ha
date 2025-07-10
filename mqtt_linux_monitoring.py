#!/usr/bin/env python3

"""
Linux System Monitoring Script with MQTT and Home Assistant Discovery
Monitors: CPU usage/temp, System load, Memory, Disk SMART, Disk I/O
Publishes to MQTT broker with Home Assistant autodiscovery
"""

import json
import time
import socket
import subprocess
import argparse
import signal
import sys
import os
import re
from typing import Dict, List, Tuple, Optional
import paho.mqtt.client as mqtt


class LinuxSystemMonitor:
    def __init__(self):
        # Configuration - Edit these variables for your setup
        self.mqtt_broker = "localhost"
        self.mqtt_port = 1883
        self.mqtt_user = ""
        self.mqtt_pass = ""
        self.mqtt_client_id = f"linux_monitor_{socket.gethostname()}"
        self.device_name = socket.gethostname()
        self.device_id = socket.gethostname().lower().replace(" ", "_")
        self.ha_discovery_prefix = "homeassistant"
        
        # Intervals (in seconds)
        self.fast_interval = 10    # CPU, memory, disk temp, disk I/O
        self.slow_interval = 3600  # Disk SMART data (1 hour)
        
        # Dry run mode
        self.dry_run = False
        
        # Global variables
        self.script_start_time = int(time.time())
        self.last_slow_update = 0
        
        # Cache variables
        self.disk_io_cache = {}
        self.cpu_usage_cache = 0
        self.disk_serial_mapping = {}  # Maps serial -> device path
        self.disk_info_cache = {}      # Maps serial -> disk info (name, model, size)
        
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
        
        if self.mqtt_user:
            self.mqtt_client.username_pw_set(self.mqtt_user, self.mqtt_pass)
        
        try:
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port, 60)
            self.mqtt_client.loop_start()
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
        
        if self.mqtt_client:
            self.mqtt_client.publish(topic, payload, retain=retain)
    
    def create_discovery_config(self, sensor_type: str, sensor_name: str, 
                              unit: str = "", device_class: str = "", 
                              state_class: str = "", icon: str = "", value_template: str = "", json_attributes_topic: str = "", json_attributes_template: str = "") -> str:
        """Create Home Assistant discovery configuration"""
        sensor_id = f"{self.device_id}_{sensor_name}"
        discovery_topic = f"{self.ha_discovery_prefix}/sensor/{sensor_id}/config"
        state_topic = f"{self.ha_discovery_prefix}/sensor/{sensor_id}/state"
        
        config = {
            "name": f"{self.device_name} {sensor_type}",
            "unique_id": sensor_id,
            "state_topic": state_topic,
            "device": {
                "identifiers": [self.device_id],
                "name": self.device_name,
                "model": "Linux System Monitor",
                "manufacturer": "Custom Script"
            }
        }
        
        if unit:
            config["unit_of_measurement"] = unit
        if device_class:
            config["device_class"] = device_class
        if state_class:
            config["state_class"] = state_class
        if icon:
            config["icon"] = icon
        if value_template:
            config["value_template"] = value_template
        if json_attributes_topic:
            config["json_attributes_topic"] = json_attributes_topic
        if json_attributes_template:
            config["json_attributes_template"] = json_attributes_template
        
        self.mqtt_publish(discovery_topic, json.dumps(config), True)
        return state_topic
    
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
            return 0.0, {}
        
        try:
            data = json.loads(output)
            
            # Navigate to the statistics data
            stats = data["sysstat"]["hosts"][0]["statistics"][0]
            
            # Extract CPU usage (100 - idle)
            cpu_data = stats.get("avg-cpu", {})
            cpu_idle = cpu_data.get("idle", 100.0)
            cpu_usage = 100.0 - cpu_idle
            
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
            
            return cpu_usage, disk_data
            
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
                        # Look for Package id 0 first (main CPU temp)
                        if "Package id 0" in sensor_data:
                            temp_input = sensor_data["Package id 0"].get("temp1_input", 0.0)
                            return {
                                "temperature": round(temp_input, 1),
                                "attrs": {
                                    "sensor": sensor_name,
                                    "max_temp": sensor_data["Package id 0"].get("temp1_max", 0.0),
                                    "crit_temp": sensor_data["Package id 0"].get("temp1_crit", 0.0),
                                }
                            }
                        
                        # Fallback to first core temperature
                        for core_name, core_data in sensor_data.items():
                            if "Core" in core_name and isinstance(core_data, dict):
                                temp_key = next((k for k in core_data.keys() if k.endswith("_input")), None)
                                if temp_key:
                                    temp_input = core_data[temp_key]
                                    return {
                                        "temperature": round(temp_input, 1),
                                        "attrs": {
                                            "sensor": f"{sensor_name} - {core_name}",
                                            "max_temp": core_data.get(temp_key.replace("_input", "_max"), 0.0),
                                            "crit_temp": core_data.get(temp_key.replace("_input", "_crit"), 0.0),\
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
        """Get memory usage data using free command with JSON output"""
        # Try free command with JSON output first
        output = self.run_command(["free", "-b", "--json"])
        if output:
            try:
                data = json.loads(output)
                
                # Extract memory and swap data
                memory_data = data.get("memory", {})
                swap_data = data.get("swap", {})
                
                # Calculate usage percentage
                mem_total = memory_data.get("total", 0)
                mem_used = memory_data.get("used", 0)
                mem_usage_percent = (mem_used / mem_total * 100.0) if mem_total > 0 else 0.0
                
                swap_total = swap_data.get("total", 0)
                swap_used = swap_data.get("used", 0)
                swap_usage_percent = (swap_used / swap_total * 100.0) if swap_total > 0 else 0.0
                
                return {
                    "mem_usage": round(mem_usage_percent, 1),
                    "mem": {
                        "total": memory_data.get("total", 0),
                        "used": memory_data.get("used", 0),
                        "free": memory_data.get("free", 0),
                        "shared": memory_data.get("shared", 0),
                        "buff_cache": memory_data.get("buff_cache", 0),
                        "available": memory_data.get("available", 0)
                    },
                    "swap_usage": round(swap_usage_percent, 1),
                    "swap": {
                        "total": swap_data.get("total", 0),
                        "used": swap_data.get("used", 0),
                        "free": swap_data.get("free", 0),
                    }
                }
                
            except (json.JSONDecodeError, KeyError, TypeError, ZeroDivisionError) as e:
                print(f"Error parsing free JSON output: {e}")
        
        # Return empty data if all methods fail
        return {
            "meme_usage": 0.0,
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
            disk_path = self.get_disk_path_by_serial(disk_or_serial)
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

    def get_disk_smart_health(self, disk_or_serial: str) -> str:
        """Get disk SMART health status (accepts device path or serial)"""
        # Determine if input is a serial or device path
        if disk_or_serial.startswith('/dev/'):
            disk_path = disk_or_serial
        else:
            disk_path = self.get_disk_path_by_serial(disk_or_serial)
            if not disk_path:
                return "UNKNOWN"
        
        output = self.run_command(["smartctl", "-H", disk_path])
        return "PASSED" if "PASSED" in output else "FAILED"
    
    def setup_discovery(self):
        """Setup Home Assistant discovery configurations"""
        print("Setting up Home Assistant discovery...")
        dev_discovery = {
            "dev": {
                "ids": self.device_id,
                "name": self.device_name,
                "sw_version": f"{self.run_command(['uname', '-v'])}",
            },
            "cmps": {
                f"{self.device_id}_last_boot": {
                    "p": "sensor",
                    "name": "Last Boot",
                    "icon":"mdi:clock",
                    "state_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_uptime/state",
                    "value_template":"{{now() - timedelta( seconds = value_json.uptime |int(0))}}",
                    "device_class":"timestamp",
                    "unique_id": f"{self.device_id}_last_boot",
                },
                f"{self.device_id}_cpu_temp": {
                    "p": "sensor",
                    "name": "CPU Temperature",
                    "unit_of_measurement": "°C",
                    "state_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_cpu_temp/state",
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
                    "state_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_avg-cpu/state",
                    "json_attributes_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_avg-cpu/state",
                    "json_attributes_template": "{{ value_json | tojson }}",
                    "value_template": "{{ 100 - (value_json.idle | float(0)) }}",
                    "icon": "mdi:processor",
                    "unique_id": f"{self.device_id}_cpu_usage",
                    "state_class": "measurement"
                },
                f"{self.device_id}_memory_usage": {
                    "p": "sensor",
                    "name": "Memory Usage",
                    "unit_of_measurement": "%",
                    "state_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_memory_usage/state",
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
                    "state_topic": f"{self.ha_discovery_prefix}/sensor/{self.device_id}_swap_usage/state",
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
        self.topics['disk_temp'] = {}
        self.topics['disk_health'] = {}
        self.topics['disk_read'] = {}
        self.topics['disk_write'] = {}
        self.topics['disk_util'] = {}
        
        # Get initial disk mapping
        disk_serials = self.get_disk_list_by_serial()
        
        for serial in disk_serials:
            display_name = self.get_disk_display_name(serial)
            safe_serial = serial.replace('-', '_').replace(' ', '_')  # Make serial safe for MQTT topics
            
            self.topics['disk_temp'][serial] = self.create_discovery_config(
                f"Disk Temperature ({display_name})", f"disk_temp_{safe_serial}", 
                "°C", "temperature", "measurement", "mdi:harddisk")
            self.topics['disk_health'][serial] = self.create_discovery_config(
                f"Disk Health ({display_name})", f"disk_health_{safe_serial}", 
                "", "", "", "mdi:harddisk")
            self.topics['disk_read'][serial] = self.create_discovery_config(
                f"Disk Read ({display_name})", f"disk_read_{safe_serial}", 
                "KB/s", "", "measurement", "mdi:harddisk")
            self.topics['disk_write'][serial] = self.create_discovery_config(
                f"Disk Write ({display_name})", f"disk_write_{safe_serial}", 
                "KB/s", "", "measurement", "mdi:harddisk")
            self.topics['disk_util'][serial] = self.create_discovery_config(
                f"Disk Utilization ({display_name})", f"disk_util_{safe_serial}", 
                "%", "", "measurement", "mdi:harddisk")
    
    def publish_onetime_sensors(self):
        """Publish one-time sensors (uptime)"""
        print("Publishing one-time sensors...")
        uptime_hours = self.get_uptime_hours()
        self.mqtt_publish(self.topics['uptime'], str(uptime_hours))
    
    def publish_slow_sensors(self):
        """Publish slow interval sensors (SMART data)"""
        print("Publishing slow interval sensors (SMART data)...")
        
        # Update disk mapping and get current serials
        disk_serials = self.get_disk_list_by_serial()
        
        for serial in disk_serials:
            if serial in self.topics['disk_health']:
                health = self.get_disk_smart_health(serial)
                self.mqtt_publish(self.topics['disk_health'][serial], health)
    
    def publish_fast_sensors(self):
        """Publish fast interval sensors"""
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Collecting iostat data ({self.fast_interval}s average)...")
        
        # Get all iostat data in one call (CPU + all disks)
        cpu_usage, disk_data = self.get_iostat_data()
        
        # Cache CPU usage
        self.cpu_usage_cache = cpu_usage
        
        # Update disk I/O cache
        self.disk_io_cache = disk_data
        
        # CPU metrics
        cpu_temp_data = self.get_cpu_temperature()
        self.mqtt_publish(self.topics['cpu_usage'], f"{cpu_usage:.1f}")
        self.mqtt_publish(self.topics['cpu_temp'], json.dumps(cpu_temp_data))
        
        # System metrics
        # system_load = self.get_system_load()
        memory_data = self.get_memory_usage()
        memory_info = self.get_memory_info()
        # self.mqtt_publish(self.topics['system_load'], f"{system_load:.2f}")
        self.mqtt_publish(self.topics['memory_usage'], json.dumps(memory_data))
        self.mqtt_publish(self.topics['memory_info'], memory_info)
        
        # Disk metrics - update mapping first
        disk_serials = self.get_disk_list_by_serial()
        
        for serial in disk_serials:
            disk_path = self.get_disk_path_by_serial(serial)
            if not disk_path:
                continue
                
            disk_name = os.path.basename(disk_path)
            
            # Only publish if we have topics for this serial (created during discovery)
            if serial in self.topics['disk_temp']:
                disk_temp = self.get_disk_temperature(serial)
                self.mqtt_publish(self.topics['disk_temp'][serial], f"{disk_temp:.1f}")
                
                # Use cached disk I/O stats
                if disk_name in self.disk_io_cache:
                    io_stats = self.disk_io_cache[disk_name]
                    self.mqtt_publish(self.topics['disk_read'][serial], f"{io_stats['read_kbs']:.1f}")
                    self.mqtt_publish(self.topics['disk_write'][serial], f"{io_stats['write_kbs']:.1f}")
                    self.mqtt_publish(self.topics['disk_util'][serial], f"{io_stats['util']:.1f}")
    
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
        
        if self.dry_run:
            print("DRY RUN MODE: Will only print MQTT messages, not publish them")
        
        # Check dependencies
        if not self.check_dependencies():
            sys.exit(1)
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.cleanup)
        signal.signal(signal.SIGTERM, self.cleanup)
        
        # Setup MQTT connection
        self.setup_mqtt()
        
        # Setup Home Assistant discovery
        self.setup_discovery()
        
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
            
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: Published sensor data (next update in {self.fast_interval}s)")
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
            
            return self.disk_serial_mapping
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Error parsing lsblk JSON output: {e}")
            return self.disk_serial_mapping

    def get_disk_list_by_serial(self) -> List[str]:
        """Get list of disk serials (updated each call)"""
        self.update_disk_mapping()
        return list(self.disk_serial_mapping.keys())

    def get_disk_path_by_serial(self, serial: str) -> str:
        """Get device path for a given serial number"""
        return self.disk_serial_mapping.get(serial, "")

    def get_disk_display_name(self, serial: str) -> str:
        """Get a human-readable name for a disk based on its serial"""
        info = self.disk_info_cache.get(serial, {})
        name = info.get("name", serial[:8])
        model = info.get("model", "Unknown")
        size = info.get("size", "")
        
        if model != "Unknown" and size:
            return f"{name} ({model} {size})"
        elif model != "Unknown":
            return f"{name} ({model})"
        else:
            return f"{name} (S/N: {serial[:8]})"
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
