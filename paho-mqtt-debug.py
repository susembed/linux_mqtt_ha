import paho.mqtt.publish as publish
import dotenv
import ssl

# Load environment variables from .env file
MQTT_BROKER = dotenv.get_key('.env', 'MQTT_BROKER')
MQTT_PORT = int(dotenv.get_key('.env', 'MQTT_PORT'))
MQTT_USERNAME = dotenv.get_key('.env', 'MQTT_USER')
MQTT_PASSWORD = dotenv.get_key('.env', 'MQTT_PASS')
MQTT_TOPIC = 'test/topic'

def send_single_message():
    auth = None
    if MQTT_USERNAME and MQTT_PASSWORD:
        auth = {'username': MQTT_USERNAME, 'password': MQTT_PASSWORD}
    
    tls = None
    if MQTT_PORT == 8883:
        tls = {'cert_reqs': ssl.CERT_REQUIRED, 'tls_version': ssl.PROTOCOL_TLS}
    
    try:
        publish.single(
            topic=MQTT_TOPIC,
            payload="Hello, this is a test message",
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            auth=auth,
            tls=tls,
            client_id='paho-mqtt-client'
        )
        print("Message sent successfully")
    except Exception as e:
        print(f"Failed to send message: {e}")

if __name__ == "__main__":
    send_single_message()
