#!/bin/bash

# Configuration script for Linux MQTT Home Assistant Monitor (Python version)
# This script helps you configure and install the Python monitoring script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/etc/linux_mqtt_ha"
SERVICE_NAME="linux-mqtt-ha"

echo "Linux MQTT Home Assistant Monitor (Python) - Configuration Script"
echo "================================================================="

# Function to check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Function to install Python dependencies
install_dependencies() {
    echo "Installing system dependencies..."
    
    # Update package list
    apt-get update
    
    # Install required packages
    apt-get install smartmontools lm-sensors sysstat
    
    # Install Python MQTT library
    # if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    #     pip3 install -r "$SCRIPT_DIR/requirements.txt"
    # else
    #     pip3 install paho-mqtt
    # fi
    
    echo "Dependencies installed successfully"
}

# Function to copy files to installation directory
install_files() {
    echo "Installing files to $INSTALL_DIR..."
    
    # Create installation directory
    mkdir -p "$INSTALL_DIR"
    
    # Copy Python script
    cp "$SCRIPT_DIR/mqtt_linux_monitoring.py" "$INSTALL_DIR/"
    if [ -f "$SCRIPT_DIR/.env" ]; then
        echo ".env already exists, skipping copy"
    else
        cp "$SCRIPT_DIR/example.env" "$INSTALL_DIR/.env"
    fi
    chmod +x "$INSTALL_DIR/mqtt_linux_monitoring.py"
    
    # Copy requirements.txt if it exists
    if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
        cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
    fi
    
    echo "Files installed successfully"
}

# Function to configure the service
configure_service() {
    echo "Configuring systemd service..."
    
    # Copy service file
    cp "$SCRIPT_DIR/linux-mqtt-ha.service" "/etc/systemd/system/"
    
    # Reload systemd
    systemctl daemon-reload
    
    echo "Service configured successfully"
}

# Function to configure MQTT settings
configure_mqtt() {
    echo ""
    echo "MQTT Configuration"
    echo "=================="
    echo "You need to configure enviroment variable in .env file."
    read -p "Would you like to edit the configuration now? (y/N): " edit_config
    
    if [[ $edit_config =~ ^[Yy]$ ]]; then
        ${EDITOR:-nano} "$INSTALL_DIR/.env"
    fi
}

# Function to test the script
test_script() {
    echo ""
    echo "Testing the script..."
    echo "==================="
    
    read -p "Would you like to run a dry-run test? (Y/n): " run_test
    
    if [[ ! $run_test =~ ^[Nn]$ ]]; then
        echo "Running dry-run test (will show MQTT messages without publishing)..."
        echo "Press Ctrl+C to stop the test"
        echo ""
        sleep 2
        
        cd "$INSTALL_DIR"
        python3 mqtt_linux_monitoring.py --dry-run &
        TEST_PID=$!
        
        # Let it run for 15 seconds
        sleep 15
        kill $TEST_PID 2>/dev/null || true
        wait $TEST_PID 2>/dev/null || true
        
        echo ""
        echo "Test completed"
    fi
}

# Function to manage the service
manage_service() {
    echo ""
    echo "Service Management"
    echo "=================="
    echo "1. Enable and start service"
    echo "2. Start service (one-time)"
    echo "3. Stop service"
    echo "4. Check service status"
    echo "5. View service logs"
    echo "6. Skip service management"
    echo ""
    
    read -p "Choose an option (1-6): " service_option
    
    case $service_option in
        1)
            systemctl enable "$SERVICE_NAME"
            systemctl start "$SERVICE_NAME"
            echo "Service enabled and started"
            ;;
        2)
            systemctl start "$SERVICE_NAME"
            echo "Service started"
            ;;
        3)
            systemctl stop "$SERVICE_NAME"
            echo "Service stopped"
            ;;
        4)
            systemctl status "$SERVICE_NAME"
            ;;
        5)
            journalctl -u "$SERVICE_NAME" -f
            ;;
        6)
            echo "Skipping service management"
            ;;
        *)
            echo "Invalid option"
            ;;
    esac
}

# Function to show usage information
show_usage() {
    echo ""
    echo "Manual Usage"
    echo "============"
    echo "To run the script manually:"
    echo "  cd $INSTALL_DIR"
    echo "  python3 mqtt_linux_monitoring.py [--dry-run]"
    echo ""
    echo "To manage the service:"
    echo "  sudo systemctl start $SERVICE_NAME"
    echo "  sudo systemctl stop $SERVICE_NAME"
    echo "  sudo systemctl status $SERVICE_NAME"
    echo "  sudo systemctl enable $SERVICE_NAME  # Start on boot"
    echo ""
    echo "To view logs:"
    echo "  sudo journalctl -u $SERVICE_NAME -f"
    echo ""
}

# Main installation flow
main() {
    check_root
    
    echo "This script will install the Linux MQTT monitoring script (Python version)"
    echo "Installation directory: $INSTALL_DIR"
    echo ""
    
    read -p "Continue with installation? (Y/n): " continue_install
    
    if [[ $continue_install =~ ^[Nn]$ ]]; then
        echo "Installation cancelled"
        exit 0
    fi
    
    install_dependencies
    install_files
    configure_service
    configure_mqtt
    test_script
    manage_service
    show_usage
    
    echo ""
    echo "Installation completed successfully!"
    echo "The Python monitoring script is now installed and ready to use."
}

# Run main function
main "$@"
