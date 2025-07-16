import paho.mqtt.client as mqtt
import dotenv
import time

# Load environment variables from .env file
MQTT_BROKER = dotenv.get_key('.env', 'MQTT_BROKER')
MQTT_PORT = int(dotenv.get_key('.env', 'MQTT_PORT'))
MQTT_USERNAME = dotenv.get_key('.env', 'MQTT_USER')
MQTT_PASSWORD = dotenv.get_key('.env', 'MQTT_PASS')
MQTT_TOPIC = 'test/topic'  # Default topic if not specified in .env
# Define the MQTT client ID
MQTT_CLIENT_ID = 'paho-mqtt-client'

# Create an MQTT client instance
client = mqtt.Client(MQTT_CLIENT_ID)

# Set authentication if credentials are provided
if MQTT_USERNAME and MQTT_PASSWORD:
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

# Define the callback function for when a message is received
def on_message(client, userdata, message):
    print(f"Received: {message.payload.decode()} on {message.topic}")

# Define the callback function for when the client connects
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected successfully")
        client.subscribe(MQTT_TOPIC)
    else:
        print(f"Connection failed with code {rc}")

# Set up the client callbacks
client.on_connect = on_connect
client.on_message = on_message

def send_test_message():
    """Send a test message to the MQTT topic"""
    test_message = "Hello, this is a test message"
    client.publish(MQTT_TOPIC, test_message)
    print("Message sent")

if __name__ == "__main__":
    try:
        # Connect to the MQTT broker
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        
        time.sleep(2)  # Wait for connection
        
        # Send test message
        send_test_message()
        
        time.sleep(5)  # Listen for messages
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Clean up
        client.loop_stop()
        client.disconnect()

# Define the callback function for when the client disconnects
def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"Unexpected disconnection from MQTT broker. Return code: {rc}")
    else:
        print("Disconnected from MQTT broker gracefully")

# Add callback for publish events
def on_publish(client, userdata, mid):
    print(f"Message published successfully. Message ID: {mid}")

# Add callback for subscribe events
def on_subscribe(client, userdata, mid, granted_qos):
    print(f"Subscription successful. Message ID: {mid}, QoS: {granted_qos}")

# Add callback for log events
def on_log(client, userdata, level, buf):
    print(f"MQTT Log [{level}]: {buf}")

# Set up the client callbacks
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect
client.on_publish = on_publish
client.on_subscribe = on_subscribe
client.on_log = on_log

# Enable logging for more debug info
client.enable_logger()

def send_test_message():
    """Send a test message to the MQTT topic"""
    if not client.is_connected():
        print("Cannot send message: Client not connected to broker")
        return
        
    test_message = "Hello, this is a test message from paho-mqtt-debug.py"
    print(f"Attempting to publish message to topic: {MQTT_TOPIC}")
    result = client.publish(MQTT_TOPIC, test_message)
    
    publish_codes = {
        0: "MQTT_ERR_SUCCESS",
        1: "MQTT_ERR_NOMEM",
        2: "MQTT_ERR_PROTOCOL",
        3: "MQTT_ERR_INVAL",
        4: "MQTT_ERR_NO_CONN",
        5: "MQTT_ERR_CONN_REFUSED",
        6: "MQTT_ERR_NOT_FOUND",
        7: "MQTT_ERR_CONN_LOST",
        8: "MQTT_ERR_TLS",
        9: "MQTT_ERR_PAYLOAD_SIZE",
        10: "MQTT_ERR_NOT_SUPPORTED",
        11: "MQTT_ERR_AUTH",
        12: "MQTT_ERR_ACL_DENIED",
        13: "MQTT_ERR_UNKNOWN",
        14: "MQTT_ERR_ERRNO"
    }
    
    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        print(f"Test message queued successfully. Message ID: {result.mid}")
    else:
        print(f"Failed to send test message. Error code: {result.rc}")
        print(f"Error: {publish_codes.get(result.rc, 'Unknown error')}")

if __name__ == "__main__":
    try:
        # Connect to the MQTT broker
        print(f"\nAttempting to connect to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")
        connect_result = client.connect(MQTT_BROKER, MQTT_PORT, 60)
        print(f"Connect attempt result: {connect_result}")
        
        # Start the network loop in a separate thread
        client.loop_start()
        
        # Wait a moment for connection to establish
        print("Waiting for connection to establish...")
        connection_timeout = 10
        for i in range(connection_timeout):
            if client.is_connected():
                print(f"Connection established after {i+1} seconds")
                break
            time.sleep(1)
            print(f"Still waiting... ({i+1}/{connection_timeout})")
        else:
            print(f"Connection failed after {connection_timeout} seconds")
        
        # Send test message
        send_test_message()
        
        # Keep the client running for a bit to receive any messages
        print("Listening for messages for 10 seconds...")
        time.sleep(10)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up
        print("Cleaning up...")
        client.loop_stop()
        client.disconnect()
        print("Client disconnected and cleaned up")
