import requests_unixsocket
import json
import time

def get_container_info(container_id):
    session = requests_unixsocket.Session()
    url = f'http+unix://%2Fvar%2Frun%2Fdocker.sock/containers/{container_id}/json'
    
    response = session.get(url)
    if response.status_code == 200:
        return json.loads(response.content.decode('utf-8'))
    else:
        raise Exception(f"Failed to get container info for {container_id}: {response.status_code}")
def get_container_stats_once(container_id):
    session = requests_unixsocket.Session()
    url = f'http+unix://%2Fvar%2Frun%2Fdocker.sock/containers/{container_id}/stats?stream=false&one-shot=true'
    
    try:
        response = session.get(url, timeout=5)  # Add timeout to prevent hanging
        if response.status_code == 200:
            return json.loads(response.content.decode('utf-8'))
        else:
            raise Exception(f"Failed to get container stats for {container_id}: {response.status_code}")
    except Exception as e:
        print(f"Error getting stats for {container_id}: {e}")
        return None

def get_all_containers_stats():
    """Alternative method: Get stats for all containers at once"""
    session = requests_unixsocket.Session()
    
    # First get list of all containers
    containers_url = 'http+unix://%2Fvar%2Frun%2Fdocker.sock/containers/json'
    response = session.get(containers_url)
    
    if response.status_code != 200:
        raise Exception(f"Failed to get containers list: {response.status_code}")
    
    containers = json.loads(response.content.decode('utf-8'))
    stats_data = {}
    
    for container in containers:
        container_id = container['Id'][:12]  # Short ID
        try:
            stats_url = f'http+unix://%2Fvar%2Frun%2Fdocker.sock/containers/{container_id}/stats?stream=false&one-shot=true'
            stats_response = session.get(stats_url, timeout=2)
            if stats_response.status_code == 200:
                stats_data[container_id] = json.loads(stats_response.content.decode('utf-8'))
        except Exception as e:
            print(f"Failed to get stats for {container_id}: {e}")
            continue
    
    return stats_data
# Usage - Original method
ids= ['b024cf9d0c2b','45004433cb59']
stats = get_container_stats_once(ids[1])
print(json.dumps(stats, indent=2))