import asyncio
import logging
import requests
import os
import socket
from bittensor import subtensor

class FirewallManager:
    def block_port(self):
        # Block external access to port 9944, but allow localhost (127.0.0.1)
        os.system("iptables -A INPUT -p tcp --dport 9944 ! -s 127.0.0.1 -j REJECT --reject-with tcp-reset")

    def unblock_port(self):
        # Unblock port 9944 to allow external access again
        os.system("iptables -D INPUT -p tcp --dport 9944 ! -s 127.0.0.1 -j REJECT --reject-with tcp-reset")

class SubtensorMonitor:
    def __init__(self, discord_webhook_url):
        self.firewall = FirewallManager()
        self.discord_webhook_url = discord_webhook_url
        self.local_address = "ws://127.0.0.1:9944"
        self.finney_address = "finney"
        self.subvortex_address = "ws://subvortex.info:9944"
        self.block_behind_threshold = 2  # Threshold for block lagging
        self.reset_script = "./reset_subtensor.sh"  # Path to the reset script
        self.logger = logging.getLogger(__name__)

    async def get_block_number(self, network):
        try:
            block = subtensor(network=network).block
            return block
        except Exception as e:
            self.report_to_discord(f"Error fetching block from {network}: {e}")
            self.logger.exception(f"Error fetching block from {network}: {e}")
            return None

    async def fetch_all_blocks(self):
        tasks = [
            self.get_block_number(self.local_address),
            self.get_block_number(self.finney_address),
            self.get_block_number(self.subvortex_address)
        ]
        return await asyncio.gather(*tasks)

    async def monitor_subtensor(self):
        is_firewalled = False
        while True:
            local_block, finney_block, subvortex_block = await self.fetch_all_blocks()

            if local_block is None:
                self.report_to_discord(f"Error: Local subtensor block could not be fetched.")
                self.logger.error("Error: Local subtensor block could not be fetched.")
                await asyncio.sleep(12)
                continue

            # Max block of external nodes (finney and subvortex)
            if finney_block is None or subvortex_block is None:
                # Retry if either external node is unreachable
                await asyncio.sleep(12)
                continue

            max_external_block = max([finney_block, subvortex_block])

            if max_external_block - local_block > self.block_behind_threshold:
                if not is_firewalled:
                    self.firewall.block_port()
                    is_firewalled = True
                    self.report_to_discord(f"Firewall applied, local subtensor is {max_external_block - local_block} blocks behind.")
                    self.logger.warning(f"Firewall applied, local subtensor is {max_external_block - local_block} blocks behind.")

            if is_firewalled and local_block >= max_external_block - 1:
                self.firewall.unblock_port()
                is_firewalled = False
                self.report_to_discord("Firewall removed, local subtensor has caught up.")
                self.logger.info("Firewall removed, local subtensor has caught up.")

            if is_firewalled:
                # If firewalled, check if block hasn't increased and reset the subtensor
                previous_local_block = local_block
                await asyncio.sleep(12)
                local_block = await self.get_block_number(self.local_address)
                if local_block is not None and local_block == previous_local_block:
                    self.report_to_discord(f"Local subtensor stuck at block {local_block}, resetting subtensor.")
                    self.reset_subtensor()

            await asyncio.sleep(12)

    def reset_subtensor(self):
        try:
            os.system(self.reset_script)
            self.report_to_discord(f"Executed reset script: {self.reset_script}")
            self.logger.info(f"Executed reset script: {self.reset_script}")
        except Exception as e:
            self.report_to_discord(f"Error executing reset script: {e}")
            self.logger.exception(f"Error executing reset script: {e}")

    def report_to_discord(self, message):
        try:
            ip_address = socket.gethostbyname(socket.gethostname())
            full_message = f"[{ip_address}] {message}"
            requests.post(self.discord_webhook_url, json={"content": full_message})
        except Exception as e:
            self.logger.exception(f"Failed to send message to Discord: {e}")


if __name__ == "__main__":
    discord_webhook_url = "YOUR_DISCORD_WEBHOOK_URL"

    monitor = SubtensorMonitor(discord_webhook_url)
    asyncio.run(monitor.monitor_subtensor())