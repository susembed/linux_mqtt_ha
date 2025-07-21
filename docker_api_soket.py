import requests_unixsocket
import json
import time

def get_container_stats_once(container_id):
    session = requests_unixsocket.Session()
    url = f'http+unix://%2Fvar%2Frun%2Fdocker.sock/containers/{container_id}/stats?stream=true'
    
    with session.get(url, stream=True) as r:
        for line in r.iter_lines():
            if line:
                return json.loads(line.decode('utf-8'))
    return None

# Usage
stats = get_container_stats_once('1743d904fa87')
print(json.dumps(stats, indent=2))
print(stats['name'].replace('/', ''))
