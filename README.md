# Linux System Monitoring with MQTT and Home Assistant

This script monitors various Linux system resources and publishes them to an MQTT broker with Home Assistant autodiscovery support.

## Features

### Monitored Metrics
- **CPU**: Usage percentage and temperature
- **Memory**: Usage percentage and used/total info
- **System**: Load average and uptime
- **Disk**: Temperature, SMART health status, I/O throughput, and utilization

### Publishing Intervals
- **One-time** (at startup): System uptime
- **Every 10 seconds**: CPU usage (averaged), memory, disk temperature, disk I/O metrics (averaged)
- **Every hour**: Disk SMART health data

**Note**: CPU usage and disk I/O metrics are now averaged over the 10-second interval using `iostat`, providing more accurate and stable readings compared to instantaneous measurements.

### Home Assistant Integration
- Automatic device discovery via MQTT Discovery
- Sensors appear automatically in Home Assistant
- Proper device grouping under a single device entity

## Dependencies

The script requires the following packages:
```bash
sudo apt-get install mosquitto-clients smartmontools lm-sensors sysstat bc
```

## Installation & Setup

1. **Clone or download the script files**
2. **Install dependencies** (see above)
3. **Configure MQTT settings**:
   ```bash
   cp config.sh my-config.sh
   nano my-config.sh  # Edit with your MQTT broker details
   ```
4. **Make scripts executable**:
   ```bash
   chmod +x mqtt_linux_monitoring.sh
   chmod +x config.sh
   ```

## Configuration

Edit the configuration variables in `config.sh` or directly in the script:

- `MQTT_BROKER`: Your MQTT broker IP/hostname
- `MQTT_PORT`: MQTT broker port (default: 1883)
- `MQTT_USER`: MQTT username (leave empty if no auth)
- `MQTT_PASS`: MQTT password (leave empty if no auth)
- `DEVICE_NAME`: How the device appears in Home Assistant
- `HA_DISCOVERY_PREFIX`: Home Assistant discovery prefix (default: "homeassistant")
- `DRY_RUN`: Set to true to only print MQTT messages without publishing (for testing)

## Testing & Debugging

### Dry Run Mode
Use dry run mode to test the script without publishing to MQTT:
```bash
./mqtt_linux_monitoring.sh --dry-run
```

This will:
- Skip dependency checks for MQTT client
- Print all MQTT topics and payloads to console
- Show retained message indicators
- Allow you to verify sensor data and Home Assistant discovery configs

### Command Line Options
- `--dry-run`: Enable dry run mode (print only, don't publish)
- `--help`: Show usage information

## Usage

### Run with configuration file:
```bash
source my-config.sh && ./mqtt_linux_monitoring.sh
```

### Run with direct configuration:
Edit the script directly and run:
```bash
./mqtt_linux_monitoring.sh
```

### Dry run mode (testing):
Test the script without actually publishing to MQTT:
```bash
./mqtt_linux_monitoring.sh --dry-run
```

### Run as a service:
Create a systemd service file `/etc/systemd/system/linux-monitor.service`:
```ini
[Unit]
Description=Linux System Monitor MQTT Publisher
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/path/to/script
ExecStartPre=/bin/bash -c 'source /path/to/my-config.sh'
ExecStart=/path/to/mqtt_linux_monitoring.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start:
```bash
sudo systemctl enable linux-monitor.service
sudo systemctl start linux-monitor.service
```

## MQTT Topics Structure

The script publishes to topics following this pattern:
```
homeassistant/sensor/{device_id}_{sensor_name}/config  # Discovery config
homeassistant/sensor/{device_id}_{sensor_name}/state   # Sensor data
```

## Home Assistant Sensors

Once running, you'll see these sensors in Home Assistant:
- `sensor.{hostname}_cpu_usage` - CPU usage percentage
- `sensor.{hostname}_cpu_temp` - CPU temperature
- `sensor.{hostname}_system_load` - System load average
- `sensor.{hostname}_memory_usage` - Memory usage percentage
- `sensor.{hostname}_memory_info` - Memory usage info (used/total)
- `sensor.{hostname}_uptime` - System uptime in hours
- `sensor.{hostname}_disk_temp_{disk}` - Temperature for each disk
- `sensor.{hostname}_disk_health_{disk}` - SMART health for each disk
- `sensor.{hostname}_disk_read_{disk}` - Read throughput for each disk
- `sensor.{hostname}_disk_write_{disk}` - Write throughput for each disk
- `sensor.{hostname}_disk_util_{disk}` - Utilization percentage for each disk

## Troubleshooting

1. **Missing sensors output**: Check if `lm-sensors` is configured:
   ```bash
   sudo sensors-detect
   ```

2. **No SMART data**: Ensure drives support SMART:
   ```bash
   sudo smartctl -i /dev/sda
   ```

3. **MQTT connection issues**: Test manually:
   ```bash
   mosquitto_pub -h your-broker -t test -m "hello"
   ```

4. **Permission issues**: Make sure the user can read system files and run smartctl

## Customization

- Modify `FAST_INTERVAL` and `SLOW_INTERVAL` to change update frequencies
- Add custom sensors by following the existing pattern
- Customize Home Assistant device information in the `create_discovery_config` function

## Security Notes

- Store MQTT credentials securely
- Consider using MQTT over TLS for production
- Run with minimal required permissions
- Regularly update dependencies for security patches
