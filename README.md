# Linux MQTT Home Assistant Monitor (Python Version)

A comprehensive Python script that monitors Linux system metrics and publishes them to an MQTT broker with Home Assistant autodiscovery support.

## Features

- **CPU Monitoring**: Usage percentage and temperature
- **Memory Monitoring**: Usage percentage and available/total memory
- **System Load**: 1-minute load average
- **Disk Monitoring**: Temperature, SMART health status, and I/O statistics (read/write speeds, utilization)
- **Home Assistant Integration**: Automatic device discovery with proper device classes and icons
- **Efficient Data Collection**: Single iostat call for CPU and all disk metrics
- **Configurable Intervals**: Fast updates (10s) for real-time metrics, slow updates (1h) for SMART data
- **Dry Run Mode**: Test configuration without publishing to MQTT
- **Systemd Service**: Run as a background service with automatic restart

## Requirements

### System Dependencies
- `smartmontools` - For disk SMART data
- `lm-sensors` - For CPU temperature
- `sysstat` - For iostat (CPU and disk I/O)
- `python3` and `python3-pip`

### Python Dependencies
- `paho-mqtt` - MQTT client library

## Quick Installation

1. **Clone or download the files**:
   ```bash
   git clone <your-repo> /tmp/linux_mqtt_ha
   cd /tmp/linux_mqtt_ha
   ```

2. **Run the configuration script**:
   ```bash
   sudo ./config-python.sh
   ```

3. **Follow the interactive prompts** to:
   - Install dependencies
   - Configure MQTT settings
   - Test the script
   - Set up the systemd service

## Manual Installation

### 1. Install Dependencies

```bash
# System packages
sudo apt-get update
sudo apt-get install python3 python3-pip smartmontools lm-sensors sysstat

# Python MQTT library
pip3 install paho-mqtt
# Or install from requirements.txt
pip3 install -r requirements.txt
```

### 2. Configure MQTT Settings

Edit the `mqtt_linux_monitoring.py` file and modify these variables in the `__init__` method:

```python
self.mqtt_broker = "your-mqtt-broker-ip"
self.mqtt_port = 1883
self.mqtt_user = "your-username"  # Leave empty if no auth
self.mqtt_pass = "your-password"  # Leave empty if no auth
```

### 3. Test the Script

Run in dry-run mode to test without publishing:
```bash
python3 mqtt_linux_monitoring.py --dry-run
```

Run normally:
```bash
python3 mqtt_linux_monitoring.py
```

### 4. Install as System Service

```bash
# Copy files to installation directory
sudo mkdir -p /opt/linux_mqtt_ha
sudo cp mqtt_linux_monitoring.py /opt/linux_mqtt_ha/
sudo cp requirements.txt /opt/linux_mqtt_ha/
sudo chmod +x /opt/linux_mqtt_ha/mqtt_linux_monitoring.py

# Install systemd service
sudo cp linux-monitor-python.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start service
sudo systemctl enable linux-monitor-python
sudo systemctl start linux-monitor-python
```

## Usage

### Command Line Options

```bash
python3 mqtt_linux_monitoring.py [--dry-run] [--help]
```

- `--dry-run`: Print MQTT topics and messages without publishing them
- `--help`: Show help message

### Service Management

```bash
# Start service
sudo systemctl start linux-monitor-python

# Stop service
sudo systemctl stop linux-monitor-python

# Check status
sudo systemctl status linux-monitor-python

# View logs
sudo journalctl -u linux-monitor-python -f

# Enable/disable automatic startup
sudo systemctl enable linux-monitor-python
sudo systemctl disable linux-monitor-python
```

## Home Assistant Integration

The script automatically creates Home Assistant discovery configurations for all sensors. Sensors will appear in Home Assistant under a device named after your hostname.

### Monitored Metrics

#### Real-time Sensors (10-second updates):
- **CPU Usage** (%)
- **CPU Temperature** (°C)
- **System Load** (1-minute average)
- **Memory Usage** (%)
- **Memory Info** (used/total in human readable format)
- **Disk Temperature** (°C) - per disk
- **Disk Read Speed** (KB/s) - per disk
- **Disk Write Speed** (KB/s) - per disk
- **Disk Utilization** (%) - per disk

#### Slow Update Sensors (1-hour updates):
- **Disk SMART Health** (PASSED/FAILED) - per disk

#### One-time Sensors:
- **System Uptime** (hours)

## Configuration

### Monitoring Intervals

You can adjust the monitoring intervals by modifying these variables in the script:

```python
self.fast_interval = 10    # CPU, memory, disk temp, disk I/O (seconds)
self.slow_interval = 3600  # Disk SMART data (seconds)
```

### MQTT Topics

The script uses this topic structure:
- Discovery: `homeassistant/sensor/{device_id}_{sensor_name}/config`
- State: `homeassistant/sensor/{device_id}_{sensor_name}/state`

### Device Information

The script identifies the device using the hostname:
- **Device Name**: `socket.gethostname()`
- **Device ID**: Hostname in lowercase with spaces replaced by underscores
- **Client ID**: `linux_monitor_{hostname}`

## Troubleshooting

### Check Dependencies
```bash
# Verify commands are available
which smartctl sensors iostat python3

# Check Python MQTT library
python3 -c "import paho.mqtt.client; print('paho-mqtt is installed')"
```

### Test MQTT Connection
```bash
# Test MQTT broker connection (install mosquitto-clients)
mosquitto_pub -h your-broker-ip -p 1883 -t test/topic -m "test message"
```

### View Detailed Logs
```bash
# Service logs
sudo journalctl -u linux-monitor-python -f

# Run manually with debug output
python3 mqtt_linux_monitoring.py --dry-run
```

### Common Issues

1. **Permission Denied for smartctl**: The script needs to run as root to access SMART data
2. **sensors command not found**: Install `lm-sensors` package
3. **iostat command not found**: Install `sysstat` package
4. **MQTT connection fails**: Check broker IP, port, and credentials

## Comparison with Bash Version

### Advantages of Python Version:
- **Better Error Handling**: More robust exception handling
- **Type Safety**: Type hints for better code maintainability
- **Object-Oriented**: Cleaner code organization
- **JSON Handling**: Native JSON support for HA discovery configs
- **Library Support**: Rich ecosystem of Python libraries
- **Cross-Platform**: Easier to extend for other platforms

### Migration from Bash Version:
- All functionality is preserved
- Configuration variables are in the `__init__` method instead of top-level variables
- Command-line arguments work the same way
- MQTT topics and Home Assistant integration are identical

## License

This project is open source. Feel free to modify and distribute according to your needs.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.
