# Linux MQTT Home Assistant Monitor (Python Version)

A comprehensive Python script that monitors Linux system metrics and publishes them to an MQTT broker with Home Assistant autodiscovery support.

## Features

- **CPU Monitoring**: Usage percentage and temperature with individual core temperatures
- **Memory Monitoring**: RAM and swap usage with detailed statistics
- **Disk Monitoring**: Temperature, SMART health status, and I/O statistics (read/write speeds, utilization)
- **Network Monitoring**: Interface speed, duplex mode, and real-time traffic monitoring
- **OS Detection**: Automatic OS information from `/etc/os-release`
- **Hardware Detection**: CPU model identification
- **Home Assistant Integration**: Automatic device discovery with proper device classes and icons
- **Environment Variables**: Configuration via `.env` file support
- **Efficient Data Collection**: Single iostat call for CPU and all disk metrics
- **Configurable Intervals**: Fast updates (10s) for real-time metrics, slow updates (1h) for SMART data
- **Dry Run Mode**: Test configuration without publishing to MQTT
- **Systemd Service**: Run as a background service with automatic restart

## Requirements

### System Dependencies
- `smartmontools` - For disk SMART data
- `lm-sensors` - For CPU temperature
- `sysstat` - For iostat (CPU and disk I/O)
- `mosquitto-clients` - For MQTT publishing
- `python3` and `python3-pip`

### Python Dependencies
- `python-dotenv` - For environment variable support (optional)

## Quick Installation

1. **Clone or download the files**:
   ```bash
   git clone <your-repo> /tmp/linux_mqtt_ha
   cd /tmp/linux_mqtt_ha
   ```

2. **Run the configuration script**:
   ```bash
   sudo ./config.sh
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
sudo apt-get install smartmontools lm-sensors sysstat mosquitto-clients python3

# Python environment variable support (optional)
pip3 install python-dotenv
```

### 2. Configure MQTT Settings

Create a `.env` file in the same directory as the script:

```bash
# MQTT Configuration
MQTT_BROKER=your-mqtt-broker-ip
MQTT_PORT=1883
MQTT_USER=your-username
MQTT_PASS=your-password

# Monitoring Configuration
FAST_INTERVAL=10
SLOW_INTERVAL=3600
NETWORK_INTERFACES=eth0,wlan0

# Home Assistant Configuration
HA_DISCOVERY_PREFIX=homeassistant
```

Or set environment variables directly:
```bash
export MQTT_BROKER="your-mqtt-broker-ip"
export MQTT_USER="your-username"
export MQTT_PASS="your-password"
export NETWORK_INTERFACES="eth0,wlan0"
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
sudo mkdir -p /etc/linux_mqtt_ha
sudo cp mqtt_linux_monitoring.py /etc/linux_mqtt_ha/
sudo cp .env /etc/linux_mqtt_ha/
sudo chmod +x /etc/linux_mqtt_ha/mqtt_linux_monitoring.py

# Install systemd service
sudo cp linux-mqtt-ha.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start service
sudo systemctl enable linux-mqtt-ha
sudo systemctl start linux-mqtt-ha
```

## Configuration

All configuration is done through environment variables, which can be set in a `.env` file or as system environment variables.

### Available Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_BROKER` | `localhost` | MQTT broker hostname or IP |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USER` | `""` | MQTT username (leave empty for no auth) |
| `MQTT_PASS` | `""` | MQTT password (leave empty for no auth) |
| `FAST_INTERVAL` | `10` | Fast sensor update interval (seconds) |
| `SLOW_INTERVAL` | `3600` | Slow sensor update interval (seconds) |
| `NETWORK_INTERFACES` | `lan` | Comma-separated list of network interfaces to monitor |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant MQTT discovery prefix |

### Example .env File

```bash
# MQTT Broker Settings
MQTT_BROKER=192.168.1.100
MQTT_PORT=1883
MQTT_USER=homeassistant
MQTT_PASS=your_secure_password

# Update Intervals
FAST_INTERVAL=10     # CPU, memory, disk I/O, network (seconds)
SLOW_INTERVAL=3600   # SMART data (seconds)

# Network Interfaces to Monitor
NETWORK_INTERFACES=eth0,wlan0,docker0

# Home Assistant Discovery
HA_DISCOVERY_PREFIX=homeassistant
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
sudo systemctl start linux-mqtt-ha

# Stop service
sudo systemctl stop linux-mqtt-ha

# Check status
sudo systemctl status linux-mqtt-ha

# View logs
sudo journalctl -u linux-mqtt-ha -f

# Enable/disable automatic startup
sudo systemctl enable linux-mqtt-ha
sudo systemctl disable linux-mqtt-ha
```

## Home Assistant Integration

The script automatically creates Home Assistant discovery configurations for all sensors. Sensors will appear in Home Assistant under a device named after your hostname with the OS version and hardware information.

### Monitored Metrics

#### Real-time Sensors (configurable interval, default 10 seconds):
- **CPU Usage** (%) with detailed breakdown (user, system, iowait, idle)
- **CPU Temperature** (°C) with individual core temperatures
- **Memory Usage** (%) with detailed memory statistics
- **Swap Usage** (%) with swap statistics
- **Network Interface Speed** (Rx/Tx in B/s) - per interface
- **Network Link Speed** (Mbit/s) and duplex mode - per interface
- **Disk Read/Write Speed** (kB/s) - per disk
- **Disk Utilization** (%) - per disk
- **Disk Usage** (%) - per disk with filesystem information

#### Slow Update Sensors (configurable interval, default 1 hour):
- **Disk SMART Health** (PASSED/FAILED) - per disk with detailed SMART attributes
- **Disk Temperature** (°C) - per disk

#### One-time Sensors:
- **Last Boot** (timestamp) - calculated from uptime

#### Device Information:
- **Device Name**: System hostname
- **Software Version**: OS pretty name from `/etc/os-release`
- **Hardware Model**: CPU model from `/proc/cpuinfo`

### MQTT Topics

The script uses this topic structure:
- Discovery: `homeassistant/device/{device_id}/config`
- State: `homeassistant/sensor/{device_id}_{sensor_name}/state`

## Troubleshooting

### Check Dependencies
```bash
# Verify commands are available
which smartctl sensors iostat mosquitto_pub python3

# Check Python dotenv library (optional)
python3 -c "import dotenv; print('python-dotenv is installed')"
```

### Test MQTT Connection
```bash
# Test MQTT broker connection
mosquitto_pub -h your-broker-ip -p 1883 -u username -P password -t test/topic -m "test message"
```

### View Detailed Logs
```bash
# Service logs
sudo journalctl -u linux-mqtt-ha -f

# Run manually with debug output
python3 mqtt_linux_monitoring.py --dry-run
```

### Common Issues

1. **Permission Denied for smartctl**: The script needs to run as root to access SMART data
2. **sensors command not found**: Install `lm-sensors` package and run `sudo sensors-detect`
3. **iostat command not found**: Install `sysstat` package
4. **mosquitto_pub command not found**: Install `mosquitto-clients` package
5. **MQTT connection fails**: Check broker IP, port, and credentials
6. **Network interface not found**: Check interface names with `ip link show` and update `NETWORK_INTERFACES`

## Features Added in Recent Updates

### Environment Variable Support
- Configuration via `.env` file
- All settings configurable without editing source code
- Fallback to system environment variables

### Enhanced Monitoring
- Network interface monitoring (speed, traffic, duplex)
- Individual CPU core temperatures
- OS information from `/etc/os-release`
- Hardware detection from `/proc/cpuinfo`
- Disk temperature sensors

### Improved Home Assistant Integration
- Better device information with OS and hardware details
- More descriptive sensor names
- Enhanced attributes for all sensors
- Proper device classes and units of measurement

## Comparison with Bash Version

### Advantages of Python Version:
- **Better Error Handling**: More robust exception handling
- **Type Safety**: Type hints for better code maintainability
- **Object-Oriented**: Cleaner code organization
- **JSON Handling**: Native JSON support for HA discovery configs
- **Environment Variables**: Easy configuration management
- **Cross-Platform**: Easier to extend for other platforms

### Migration from Bash Version:
- All functionality is preserved and enhanced
- Configuration moved to environment variables/`.env` file
- Command-line arguments work the same way
- MQTT topics and Home Assistant integration are improved

## License

This project is open source. Feel free to modify and distribute according to your needs.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

### TODO Features
- Using native paho-mqtt python instead `mosquitto_pub` command
- Docker container monitoring
- Custom sensor configurations
- GPU monitoring support (this can be tricky)
