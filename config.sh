#!/bin/bash

# Configuration script for Linux MQTT Home Assistant Monitor (Python version)
# This script helps you configure, install, and update the Python monitoring script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/etc/linux_mqtt_ha"
SERVICE_NAME="linux-mqtt-ha"
REPO_URL="https://github.com/susembed/linux_mqtt_ha.git"

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
    read -p "This script will install system dependencies. Do you want to continue? (Y/n): " install_deps
    if [[ $install_deps =~ ^[Nn]$ ]]; then
        echo "Skipping dependency installation"
        return
    fi
    echo "Installing system dependencies..."
    
    # Update package list
    sudo apt-get update
    
    # Install required packages
    sudo apt-get install smartmontools lm-sensors sysstat hdparm python3-dotenv
    
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
    sudo mkdir -p "$INSTALL_DIR"
    
    # Copy Python script
    cp "$SCRIPT_DIR/mqtt_linux_monitoring.py" "$INSTALL_DIR/"
    if [ -f "$INSTALL_DIR/.env" ]; then
        echo ".env already exists, skipping copy"
    else
        sudo cp "$SCRIPT_DIR/example.env" "$INSTALL_DIR/.env"
    fi
    sudo chmod +x "$INSTALL_DIR/mqtt_linux_monitoring.py"
    
    # Copy requirements.txt if it exists
    if [ -f "$INSTALL_DIR/requirements.txt" ]; then
        sudo cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
    fi
    
    echo "Files installed successfully"
}

# Function to configure the service
configure_service() {
    echo "Configuring systemd service..."
    
    # Copy service file
    sudo cp "$SCRIPT_DIR/linux-mqtt-ha.service" "/etc/systemd/system/"
    
    # Reload systemd
    sudo systemctl daemon-reload
    
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
        sudo ${EDITOR:-nano} "$INSTALL_DIR/.env"
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
        sudo python3 mqtt_linux_monitoring.py --dry-run &
        TEST_PID=$!
        
        # Let it run for 15 seconds
        sleep 15
        sudo kill $TEST_PID 2>/dev/null || true
        sudo wait $TEST_PID 2>/dev/null || true
        
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
            sudo systemctl enable "$SERVICE_NAME"
            sudo systemctl start "$SERVICE_NAME"
            echo "Service enabled and started"
            ;;
        2)
            sudo systemctl start "$SERVICE_NAME"
            echo "Service started"
            ;;
        3)
            sudo systemctl stop "$SERVICE_NAME"
            echo "Service stopped"
            ;;
        4)
            sudo systemctl status "$SERVICE_NAME"
            ;;
        5)
            sudo journalctl -u "$SERVICE_NAME" -f
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

# Function to update the script
update_script() {
    echo "Updating Linux MQTT monitoring script..."
    echo "========================================"
    echo "Pulling latest changes from repository..."
    cd "$SCRIPT_DIR"
    git pull || {
        echo "Error: Failed to update from repository"
        exit 1
    }
    echo "Elevating privileges to copy updated files..."
    sudo echo "Elevated to root"
    # Check if installation directory exists
    if [ ! -d "$INSTALL_DIR" ]; then
        echo "Error: Installation directory $INSTALL_DIR does not exist"
        echo "Please run the full installation first"
        exit 1
    fi
    
    # Check if Python script exists in source
    if [ ! -f "$SCRIPT_DIR/mqtt_linux_monitoring.py" ]; then
        echo "Error: Source script $SCRIPT_DIR/mqtt_linux_monitoring.py not found"
        exit 1
    fi
    
    echo "Copying updated Python script to $INSTALL_DIR..."
    sudo cp "$SCRIPT_DIR/mqtt_linux_monitoring.py" "$INSTALL_DIR/"
    sudo chmod +x "$INSTALL_DIR/mqtt_linux_monitoring.py"
    
    # Copy requirements.txt if it exists
    if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
        sudo cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
        echo "Updated requirements.txt"
    fi
    
    echo "Restarting service..."
    if  sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        sudo systemctl restart "$SERVICE_NAME"
        echo "Service restarted successfully"
    else
        echo "Service was not running, starting it now..."
        sudo systemctl start "$SERVICE_NAME"
        echo "Service started successfully"
    fi
    
    echo ""
    echo "Update completed successfully!"
    sudo systemctl status "$SERVICE_NAME" --no-pager
}

# Function to show script usage
show_script_usage() {
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  install    Run the full installation process (default)"
    echo "  update     Update the Python script and restart service"
    echo "  help       Show this help message"
    echo ""
    echo "Examples:"
    echo "  sudo $0                # Full installation"
    echo "  sudo $0 install        # Full installation"
    echo "  sudo $0 update         # Update script only"
    echo ""
}

# Main installation flow
main_install() {
    echo "This script will install the Linux MQTT monitoring script (Python version)"
    echo "Installation directory: $INSTALL_DIR"
    echo ""
    echo "Elevating privileges to copy updated files..."
    sudo echo "Elevated to root"
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

# Main function with command handling
main() {
    # check_root
    
    case "${1:-install}" in
        install)
            main_install
            ;;
        update)
            update_script
            ;;
        help|--help|-h)
            show_script_usage
            ;;
        *)
            echo "Error: Unknown command '$1'"
            echo ""
            show_script_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
