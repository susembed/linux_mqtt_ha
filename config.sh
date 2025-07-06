#!/bin/bash

# Configuration file for Linux System Monitor
# Copy this file and modify the values according to your setup

# MQTT Broker Configuration
export MQTT_BROKER="your-mqtt-broker-ip"     # e.g., "192.168.1.100" or "localhost"
export MQTT_PORT="1883"                      # Default MQTT port
export MQTT_USER="your-mqtt-username"        # Leave empty if no authentication
export MQTT_PASS="your-mqtt-password"        # Leave empty if no authentication
export MQTT_CLIENT_ID="linux_monitor_$(hostname)"

# Device Configuration
export DEVICE_NAME="$(hostname)"             # Will appear in Home Assistant
export DEVICE_ID="$(hostname | tr '[:upper:]' '[:lower:]' | tr ' ' '_')"

# Home Assistant Discovery Configuration
export HA_DISCOVERY_PREFIX="homeassistant"   # Default HA discovery prefix

# Monitoring Intervals (in seconds)
export FAST_INTERVAL=10                      # CPU, memory, disk temp, disk I/O
export SLOW_INTERVAL=3600                    # Disk SMART data (1 hour)

# Dry run mode - set to true to only print MQTT messages without publishing
export DRY_RUN=false                         # Set to true for testing/debugging

# Example usage:
# 1. Copy this file: cp config.sh my-config.sh
# 2. Edit my-config.sh with your settings
# 3. Source it before running: source my-config.sh && ./mqtt_linux_monitoring.sh
# 4. For dry run testing: ./mqtt_linux_monitoring.sh --dry-run
