import requests
import subprocess
import socket
import time
import logging
from bittensor import subtensor
from concurrent.futures import ThreadPoolExecutor, TimeoutError

# Constants
SUBVORTEX_URL = 'ws://subvortex.info:9944'
FINNEY_NETWORK = 'finney'
LOCAL_URL = 'ws://localhost:9944'
CHECK_INTERVAL = 12  # seconds
TIMEOUT = 10  # seconds for the connections
DISCORD_WEBHOOK_URL = 'https://discord.com/api/webhooks/YOUR_WEBHOOK_URL'

# Function to send Discord message
def send_discord_message(message):
    data = {"content": message}
    logging.info(f"Sending Discord message: {message}")
    # try:
    #     requests.post(DISCORD_WEBHOOK_URL, json=data)
    # except Exception as e:
    #     logging.error(f"Failed to send Discord message: {e}")

# Get the local IP address
def get_local_ip():
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except Exception as e:
        logging.error(f"Failed to get local IP address: {e}")
        return "Unknown"

# Function to get the block number
def get_block_number(network_url):
    try:
        st = subtensor(network=network_url)
        return st.block
    except Exception as e:
        logging.error(f"Error fetching block number from {network_url}: {e}")
        return None
# Function to get block number with a custom timeout
def get_block_number_with_custom_timeout(network_url, timeout=TIMEOUT):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(get_block_number, network_url)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            logging.error(f"Timeout occurred while fetching block number from {network_url}")
            return None
        except Exception as e:
            logging.error(f"An error occurred: {e}")
            return None

# Function to apply firewall rule but allow localhost access
def firewall_subtensor_allow_local():
    # Block port 9944 for all IPs except localhost (127.0.0.1)
    subprocess.run(["sudo", "iptables", "-A", "OUTPUT", "-p", "tcp", "--dport", "9944", "!", "-d", "127.0.0.1", "-j", "DROP"])
    send_discord_message(f"Firewall applied to subtensor (blocking external access) at {get_local_ip()}")

# Function to remove firewall rule
def remove_firewall():
    subprocess.run(["sudo", "iptables", "-D", "OUTPUT", "-p", "tcp", "--dport", "9944", "!", "-d", "127.0.0.1", "-j", "DROP"])
    send_discord_message(f"Firewall removed for subtensor at {get_local_ip()}")

# Function to reset the subtensor
def reset_subtensor():
    subprocess.run(["/path/to/reset_script.sh"])
    send_discord_message(f"Subtensor reset initiated at {get_local_ip()}")

# Main monitoring loop
def monitor_subtensor():
    firewall_active = False

    while True:
        try:
            # Get block numbers
            local_block = get_block_number_with_custom_timeout(LOCAL_URL)
            subvortex_block = get_block_number_with_custom_timeout(SUBVORTEX_URL)
            finney_block = get_block_number_with_custom_timeout(FINNEY_NETWORK)
            print(f"Local block: {local_block}, Subvortex block: {subvortex_block}, Finney block: {finney_block}")
            
            # Find max external block
            external_blocks = [b for b in [subvortex_block, finney_block] if b is not None]
            if not external_blocks:
                # If neither external node is available, wait and retry
                time.sleep(CHECK_INTERVAL)
                continue

            max_external_block = max(external_blocks)

            # Check local block progress
            if local_block is None or max_external_block - local_block >= 2:
                # Block is behind or cannot read block
                if not firewall_active:
                    firewall_subtensor_allow_local()
                    reset_subtensor()
                    firewall_active = True

            elif firewall_active and local_block >= max_external_block - 1:
                # Local block caught up, remove firewall
                remove_firewall()
                firewall_active = False

        except Exception as e:
            logging.error(f"Error during monitoring: {e}")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    monitor_subtensor()
