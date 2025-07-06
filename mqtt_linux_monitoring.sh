#!/bin/bash

# Linux System Monitoring Script with MQTT and Home Assistant Discovery
# Monitors: CPU usage/temp, System load, Memory, Disk SMART, Disk I/O
# Publishes to MQTT broker with Home Assistant autodiscovery

# Configuration - Edit these variables for your setup
MQTT_BROKER="localhost"
MQTT_PORT="1883"
MQTT_USER=""
MQTT_PASS=""
MQTT_CLIENT_ID="linux_monitor_$(hostname)"
DEVICE_NAME="$(hostname)"
DEVICE_ID="$(hostname | tr '[:upper:]' '[:lower:]' | tr ' ' '_')"
HA_DISCOVERY_PREFIX="homeassistant"

# Intervals (in seconds)
FAST_INTERVAL=10    # CPU, memory, disk temp, disk I/O
SLOW_INTERVAL=3600  # Disk SMART data (1 hour)

# Dry run mode - set to true to only print MQTT messages without publishing
DRY_RUN=false

# Global variables
SCRIPT_START_TIME=$(date +%s)
LAST_SLOW_UPDATE=0

# Cache variables for iostat data
declare -A DISK_IO_CACHE
CPU_USAGE_CACHE=0

# Function to check dependencies
check_dependencies() {
    local missing_deps=()
    
    if [ "$DRY_RUN" = "true" ]; then
        echo "DRY RUN MODE: Skipping dependency checks"
        return 0
    fi
    
    command -v mosquitto_pub >/dev/null 2>&1 || missing_deps+=("mosquitto-clients")
    command -v smartctl >/dev/null 2>&1 || missing_deps+=("smartmontools")
    command -v sensors >/dev/null 2>&1 || missing_deps+=("lm-sensors")
    command -v iostat >/dev/null 2>&1 || missing_deps+=("sysstat")
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        echo "Missing dependencies: ${missing_deps[*]}"
        echo "Please install them with: sudo apt-get install ${missing_deps[*]}"
        exit 1
    fi
}

# Function to publish MQTT message
mqtt_publish() {
    local topic="$1"
    local payload="$2"
    local retain="${3:-false}"
    
    if [ "$DRY_RUN" = "true" ]; then
        local retain_flag=""
        if [ "$retain" = "true" ]; then
            retain_flag=" [RETAINED]"
        fi
        echo "[DRY RUN]$retain_flag Topic: $topic"
        echo "[DRY RUN]$retain_flag Payload: $payload"
        echo "---"
        return 0
    fi
    
    local mqtt_cmd="mosquitto_pub -h $MQTT_BROKER -p $MQTT_PORT -t \"$topic\" -m \"$payload\""
    
    if [ "$retain" = "true" ]; then
        mqtt_cmd="$mqtt_cmd -r"
    fi
    
    if [ -n "$MQTT_USER" ]; then
        mqtt_cmd="$mqtt_cmd -u \"$MQTT_USER\""
    fi
    
    if [ -n "$MQTT_PASS" ]; then
        mqtt_cmd="$mqtt_cmd -P \"$MQTT_PASS\""
    fi
    
    eval $mqtt_cmd
}

# Function to create Home Assistant discovery config
create_discovery_config() {
    local sensor_type="$1"
    local sensor_name="$2"
    local unit="$3"
    local device_class="$4"
    local state_class="$5"
    local icon="$6"
    
    local sensor_id="${DEVICE_ID}_${sensor_name}"
    local discovery_topic="$HA_DISCOVERY_PREFIX/sensor/$sensor_id/config"
    local state_topic="$HA_DISCOVERY_PREFIX/sensor/$sensor_id/state"
    
    local config_json="{
        \"name\": \"$DEVICE_NAME $sensor_type\",
        \"unique_id\": \"$sensor_id\",
        \"state_topic\": \"$state_topic\",
        \"device\": {
            \"identifiers\": [\"$DEVICE_ID\"],
            \"name\": \"$DEVICE_NAME\",
            \"model\": \"Linux System Monitor\",
            \"manufacturer\": \"Custom Script\"
        }"
    
    if [ -n "$unit" ]; then
        config_json="$config_json,\"unit_of_measurement\": \"$unit\""
    fi
    
    if [ -n "$device_class" ]; then
        config_json="$config_json,\"device_class\": \"$device_class\""
    fi
    
    if [ -n "$state_class" ]; then
        config_json="$config_json,\"state_class\": \"$state_class\""
    fi
    
    if [ -n "$icon" ]; then
        config_json="$config_json,\"icon\": \"$icon\""
    fi
    
    config_json="$config_json}"
    
    mqtt_publish "$discovery_topic" "$config_json" true
    echo "$state_topic"
}

# Function to get iostat data (CPU and disk metrics)
# Returns data in format: cpu_usage,disk1_read_kbs,disk1_write_kbs,disk1_util,disk2_read_kbs,disk2_write_kbs,disk2_util,...
get_iostat_data() {
    local iostat_output=$(iostat -d $FAST_INTERVAL 1 -y -c -x 2>/dev/null)
    
    # Extract CPU usage (100 - idle)
    local cpu_idle=$(echo "$iostat_output" | grep -A1 "avg-cpu:" | tail -1 | awk '{print $6}')
    local cpu_usage=$(echo "scale=1; 100 - $cpu_idle" | bc)
    
    # Start building result with CPU usage
    local result="$cpu_usage"
    
    # Extract disk data
    local disk_data=$(echo "$iostat_output" | awk '
        /^Device/ { found_device_header = 1; next }
        found_device_header && /^[a-z]/ {
            # Extract: device, read_kbs, write_kbs, util
            printf "%s,%.1f,%.1f,%.1f\n", $1, $3, $8, $NF
        }
    ')
    
    # Add disk data to result
    if [ -n "$disk_data" ]; then
        while IFS=',' read -r device read_kbs write_kbs util; do
            result="$result,$device,$read_kbs,$write_kbs,$util"
        done <<< "$disk_data"
    fi
    
    echo "$result"
}

# Function to get CPU usage (extracted from iostat)
get_cpu_usage() {
    # This will be populated by get_iostat_data, keeping for backward compatibility
    echo "${CPU_USAGE_CACHE:-0}"
}

# Function to get CPU temperature
get_cpu_temp() {
    local temp=$(sensors 2>/dev/null | grep -E "Package id 0|Core 0" | head -1 | grep -oE '\+[0-9]+\.[0-9]+°C' | grep -oE '[0-9]+\.[0-9]+' | head -1)
    if [ -z "$temp" ]; then
        # Fallback to thermal zone
        temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
        if [ -n "$temp" ]; then
            temp=$(echo "scale=1; $temp/1000" | bc)
        fi
    fi
    echo "${temp:-0}"
}

# Function to get system load
get_system_load() {
    uptime | awk -F'load average:' '{print $2}' | awk '{gsub(/,/, ""); print $1}'
}

# Function to get memory usage
get_memory_usage() {
    free | grep Mem | awk '{printf "%.1f", $3/$2 * 100.0}'
}

# Function to get memory info
get_memory_info() {
    free -h | grep Mem | awk '{print $3 "/" $2}'
}

# Function to get uptime in hours
get_uptime_hours() {
    awk '{print int($1/3600)}' /proc/uptime
}

# Function to get disk list
get_disk_list() {
    lsblk -dpno NAME,TYPE | awk '$2=="disk" {print $1}' | grep -E '^/dev/(sd|nvme|hd)' | sort
}

# Function to get disk temperature
get_disk_temp() {
    local disk="$1"
    local temp=$(smartctl -A "$disk" 2>/dev/null | awk '/Temperature_Celsius/ {print $10}' | head -1)
    if [ -z "$temp" ]; then
        temp=$(smartctl -A "$disk" 2>/dev/null | awk '/Airflow_Temperature_Cel/ {print $10}' | head -1)
    fi
    echo "${temp:-0}"
}

# Function to get disk SMART health
get_disk_smart_health() {
    local disk="$1"
    smartctl -H "$disk" 2>/dev/null | grep -q "PASSED" && echo "PASSED" || echo "FAILED"
}

# Function to get disk I/O stats (extracted from iostat)
get_disk_io_stats() {
    local disk="$1"
    local disk_name=$(basename "$disk")
    
    # Return cached data from iostat if available
    if [ -n "${DISK_IO_CACHE[$disk_name]}" ]; then
        echo "${DISK_IO_CACHE[$disk_name]}"
        return
    fi
    
    # Fallback to individual iostat call (less efficient)
    iostat -x 1 2 | grep "$disk_name" | tail -1 | awk '{print $4 "," $5 "," $6 "," $10}'
}

# Function to setup discovery configs
setup_discovery() {
    echo "Setting up Home Assistant discovery..."
    
    # CPU sensors
    CPU_USAGE_TOPIC=$(create_discovery_config "CPU Usage" "cpu_usage" "%" "" "measurement" "mdi:processor")
    CPU_TEMP_TOPIC=$(create_discovery_config "CPU Temperature" "cpu_temp" "°C" "temperature" "measurement" "mdi:thermometer")
    
    # System sensors
    LOAD_TOPIC=$(create_discovery_config "System Load" "system_load" "" "" "measurement" "mdi:gauge")
    MEMORY_USAGE_TOPIC=$(create_discovery_config "Memory Usage" "memory_usage" "%" "" "measurement" "mdi:memory")
    MEMORY_INFO_TOPIC=$(create_discovery_config "Memory Info" "memory_info" "" "" "" "mdi:memory")
    UPTIME_TOPIC=$(create_discovery_config "Uptime" "uptime" "h" "duration" "total_increasing" "mdi:clock")
    
    # Disk sensors
    declare -g -A DISK_TEMP_TOPICS
    declare -g -A DISK_HEALTH_TOPICS
    declare -g -A DISK_READ_TOPICS
    declare -g -A DISK_WRITE_TOPICS
    declare -g -A DISK_UTIL_TOPICS
    
    for disk in $(get_disk_list); do
        local disk_name=$(basename "$disk")
        DISK_TEMP_TOPICS["$disk"]=$(create_discovery_config "Disk Temperature ($disk_name)" "disk_temp_$disk_name" "°C" "temperature" "measurement" "mdi:harddisk")
        DISK_HEALTH_TOPICS["$disk"]=$(create_discovery_config "Disk Health ($disk_name)" "disk_health_$disk_name" "" "" "" "mdi:harddisk")
        DISK_READ_TOPICS["$disk"]=$(create_discovery_config "Disk Read ($disk_name)" "disk_read_$disk_name" "KB/s" "" "measurement" "mdi:harddisk")
        DISK_WRITE_TOPICS["$disk"]=$(create_discovery_config "Disk Write ($disk_name)" "disk_write_$disk_name" "KB/s" "" "measurement" "mdi:harddisk")
        DISK_UTIL_TOPICS["$disk"]=$(create_discovery_config "Disk Utilization ($disk_name)" "disk_util_$disk_name" "%" "" "measurement" "mdi:harddisk")
    done
}

# Function to publish one-time sensors (uptime)
publish_onetime_sensors() {
    echo "Publishing one-time sensors..."
    local uptime_hours=$(get_uptime_hours)
    mqtt_publish "$UPTIME_TOPIC" "$uptime_hours"
}

# Function to publish slow interval sensors (SMART data)
publish_slow_sensors() {
    echo "Publishing slow interval sensors (SMART data)..."
    
    for disk in $(get_disk_list); do
        local health=$(get_disk_smart_health "$disk")
        mqtt_publish "${DISK_HEALTH_TOPICS[$disk]}" "$health"
    done
}

# Function to publish fast interval sensors
publish_fast_sensors() {
    echo "$(date): Collecting iostat data (${FAST_INTERVAL}s average)..."
    
    # Get all iostat data in one call (CPU + all disks)
    local iostat_data=$(get_iostat_data)
    
    # Parse iostat data
    IFS=',' read -ra IOSTAT_ARRAY <<< "$iostat_data"
    local cpu_usage="${IOSTAT_ARRAY[0]}"
    
    # Cache CPU usage for get_cpu_usage function
    CPU_USAGE_CACHE="$cpu_usage"
    
    # Clear and populate disk I/O cache
    declare -g -A DISK_IO_CACHE
    local i=1
    while [ $i -lt ${#IOSTAT_ARRAY[@]} ]; do
        local device="${IOSTAT_ARRAY[$i]}"
        local read_kbs="${IOSTAT_ARRAY[$((i+1))]}"
        local write_kbs="${IOSTAT_ARRAY[$((i+2))]}"
        local util="${IOSTAT_ARRAY[$((i+3))]}"
        DISK_IO_CACHE["$device"]="$read_kbs,$write_kbs,0,$util"
        i=$((i+4))
    done
    
    # CPU metrics
    local cpu_temp=$(get_cpu_temp)
    mqtt_publish "$CPU_USAGE_TOPIC" "$cpu_usage"
    mqtt_publish "$CPU_TEMP_TOPIC" "$cpu_temp"
    
    # System metrics
    local system_load=$(get_system_load)
    local memory_usage=$(get_memory_usage)
    local memory_info=$(get_memory_info)
    mqtt_publish "$LOAD_TOPIC" "$system_load"
    mqtt_publish "$MEMORY_USAGE_TOPIC" "$memory_usage"
    mqtt_publish "$MEMORY_INFO_TOPIC" "$memory_info"
    
    # Disk metrics
    for disk in $(get_disk_list); do
        local disk_name=$(basename "$disk")
        local disk_temp=$(get_disk_temp "$disk")
        mqtt_publish "${DISK_TEMP_TOPICS[$disk]}" "$disk_temp"
        
        # Use cached disk I/O stats
        if [ -n "${DISK_IO_CACHE[$disk_name]}" ]; then
            local io_stats="${DISK_IO_CACHE[$disk_name]}"
            local read_kbs=$(echo "$io_stats" | cut -d',' -f1)
            local write_kbs=$(echo "$io_stats" | cut -d',' -f2)
            local util=$(echo "$io_stats" | cut -d',' -f4)
            
            mqtt_publish "${DISK_READ_TOPICS[$disk]}" "$read_kbs"
            mqtt_publish "${DISK_WRITE_TOPICS[$disk]}" "$write_kbs"
            mqtt_publish "${DISK_UTIL_TOPICS[$disk]}" "$util"
        fi
    done
}

# Function to handle script termination
cleanup() {
    echo "Cleaning up..."
    exit 0
}

# Main function
main() {
    echo "Starting Linux System Monitor for $DEVICE_NAME"
    echo "MQTT Broker: $MQTT_BROKER:$MQTT_PORT"
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dry-run)
                DRY_RUN=true
                echo "DRY RUN MODE: Will only print MQTT messages, not publish them"
                shift
                ;;
            -h|--help)
                echo "Usage: $0 [--dry-run] [--help]"
                echo "  --dry-run    Print MQTT topics and messages without publishing"
                echo "  --help       Show this help message"
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
    
    # Check dependencies
    check_dependencies
    
    # Setup signal handlers
    trap cleanup SIGINT SIGTERM
    
    # Setup Home Assistant discovery
    setup_discovery
    
    # Publish one-time sensors
    publish_onetime_sensors
    echo "########## Starting monitoring loop ##########"
    # Main monitoring loop
    while true; do
        local current_time=$(date +%s)
        
        # Check if it's time for slow sensors update
        if [ $((current_time - LAST_SLOW_UPDATE)) -ge $SLOW_INTERVAL ]; then
            publish_slow_sensors
            LAST_SLOW_UPDATE=$current_time
        fi
        
        # Publish fast sensors (includes built-in sleep via iostat)
        publish_fast_sensors
        
        echo "$(date): Published sensor data (next update in ${FAST_INTERVAL}s)"
        # No need for sleep here as iostat already waits FAST_INTERVAL seconds
    done
}

# Run main function if script is executed directly
if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    main "$@"
fi