import asyncio
import json
import logging
import socket
import subprocess

import docker
import requests
from bittensor import subtensor


class FirewallManager:
    def __init__(self, port: int = 9944):
        self.port = port

    def block_port(self):
        # Block external access to port 9944, but allow localhost (127.0.0.1)
        subprocess.run(
            [
                "iptables",
                "-I",
                "DOCKER-USER",
                "-p",
                "tcp",
                "--dport",
                str(self.port),
                "!",
                "-s",
                "127.0.0.1",
                "-j",
                "REJECT",
            ],
            capture_output=True,
            check=False,
        )

    def unblock_port(self):
        # Unblock port 9944 to allow external access again
        subprocess.run(
            [
                "iptables",
                "-D",
                "DOCKER-USER",
                "-p",
                "tcp",
                "--dport",
                str(self.port),
                "!",
                "-s",
                "127.0.0.1",
                "-j",
                "REJECT",
            ],
            capture_output=True,
            check=False,
        )


class SubtensorMonitor:
    def __init__(
        self,
        discord_webhook_url: str,
        subtensor_container_name: str,
        subtensor_volume_name: str,
        local_url: str,
        finney_url: str,
        subvortex_url: str,
    ):
        self.local_port = local_url.split(":")[-1]

        self.firewall = FirewallManager(self.local_port)
        self.discord_webhook_url = discord_webhook_url
        self.local_url = local_url
        self.finney_url = finney_url
        self.subvortex_url = subvortex_url
        self.block_behind_threshold = 2  # Threshold for block lagging
        self.syncing_detected = False
        self.stuck_threshold = 60  # 1 minute threshold for stuck detection
        self.previous_local_block = None
        self.previous_finney_block = None
        self.previous_subvortex_block = None
        self.stuck_start_time = None
        self.docker_client = docker.from_env()
        self.subtensor_container_name = subtensor_container_name
        self.subtensor_volume_name = subtensor_volume_name

        # Configure logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

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
            self.get_block_number(self.local_url),
            self.get_block_number(self.finney_url),
            self.get_block_number(self.subvortex_url),
        ]
        return await asyncio.gather(*tasks)
    
    def is_syncing(self):
        payload = {"jsonrpc": "2.0", "method": "system_health", "params": [], "id": 1}
        headers = {"Content-Type": "application/json"}
        rpc_endpoint = self.local_url.replace("ws://", "http://").replace("wss://", "https://")

        response = requests.post(rpc_endpoint, headers=headers, data=json.dumps(payload), timeout=30)
        if response.status_code == 200:
            result = response.json()
            if "result" in result and "isSyncing" in result["result"]:
                is_syncing = result["result"]["isSyncing"]
                if is_syncing:
                    return True
                else:
                    return False
        raise Exception(f"Failed to fetch system health data.{response.text}")

    def auto_heal_subtensor(self):
        try:
            subtensor_container = self.docker_client.containers.get(self.subtensor_container_name)
            subtensor_container.stop()
            self.logger.info(f"Stopped container {self.subtensor_container_name}.")

            subtensor_container.remove(force=True)

            subtensor_volume = self.docker_client.volumes.get(self.subtensor_volume_name)
            subtensor_volume.remove(force=True)

            port_bindings = subtensor_container.attrs["HostConfig"].get("PortBindings", {})
            ports = {port: port.split("/")[0] for port in port_bindings}
            volume = self.subtensor_container_name + ":" + subtensor_container.attrs["Mounts"][0]["Destination"]
            self.docker_client.containers.run(
                image=subtensor_container.image.tags[0],
                name=subtensor_container.name,
                detach=True,
                cpu_count=subtensor_container.attrs["HostConfig"]["CpuCount"],
                mem_limit=subtensor_container.attrs["HostConfig"]["Memory"],
                memswap_limit=subtensor_container.attrs["HostConfig"]["MemorySwap"],
                ports=ports,
                environment=subtensor_container.attrs["Config"]["Env"],
                volumes=[volume],
                command=subtensor_container.attrs["Config"]["Cmd"],
                network=subtensor_container.attrs["HostConfig"]["NetworkMode"],
            )

            self.report_to_discord("Subtensor auto-healed (container restarted and volume wiped).")
            self.logger.info("Subtensor auto-healed (container restarted and volume wiped).")
        except docker.errors.NotFound:
            self.report_to_discord("Subtensor container or volume not found.")
            self.logger.error("Subtensor container or volume not found.")
        except Exception as e:
            self.report_to_discord(f"Auto-healing failed: {e}")
            self.logger.exception(f"Auto-healing failed: {e}")

    def report_to_discord(self, message):
        try:
            ip_address = socket.gethostbyname(socket.gethostname())
            full_message = f"[{ip_address}] {message}"
            requests.post(self.discord_webhook_url, json={"content": full_message}, timeout=30)
        except Exception as e:
            self.logger.exception(f"Failed to send message to Discord: {e}")

    async def monitor_subtensor(self):
        is_firewalled = False
        while True:
            local_block, finney_block, subvortex_block = await self.fetch_all_blocks()

            if local_block is None:
                self.report_to_discord("Error: Local subtensor block could not be fetched.")
                self.logger.error("Error: Local subtensor block could not be fetched.")
                await asyncio.sleep(12)
                continue

            # Max block of external nodes (finney and subvortex)
            if finney_block is None or subvortex_block is None:
                await asyncio.sleep(12)
                continue

            self.logger.info(
                f"Local block: {local_block}, Finney block: {finney_block}, Subvortex block: {subvortex_block}"
            )

            if self.is_syncing():
                if not self.syncing_detected:
                    self.firewall.block_port()
                    self.syncing_detected = True
                self.report_to_discord("Node is syncing, skipping block comparison.")
                self.logger.warning("Node is syncing, skipping block comparison.")
                await asyncio.sleep(12)
                continue
            else:
                if self.syncing_detected:
                    self.firewall.unblock_port()
                    self.report_to_discord("State sync is complete, firewall removed.")
                    self.logger.info("State sync is complete, firewall removed.")
                self.syncing_detected = False

            max_external_block = max([finney_block, subvortex_block])

            if max_external_block - local_block > self.block_behind_threshold:
                if not is_firewalled:
                    self.firewall.block_port()
                    is_firewalled = True
                    self.report_to_discord(
                        f"Firewall applied, local subtensor is {max_external_block - local_block} blocks behind."
                    )
                    self.logger.warning(
                        f"Firewall applied, local subtensor is {max_external_block - local_block} blocks behind."
                    )

            if is_firewalled and local_block >= max_external_block - 1:
                self.firewall.unblock_port()
                is_firewalled = False
                self.report_to_discord("Firewall removed, local subtensor has caught up.")
                self.logger.info("Firewall removed, local subtensor has caught up.")

            if is_firewalled:
                if (
                    local_block == self.previous_local_block
                    and self.previous_finney_block < finney_block
                    and self.previous_subvortex_block < subvortex_block
                ):
                    if self.stuck_start_time is None:
                        self.stuck_start_time = asyncio.get_event_loop().time()
                        self.logger.warning(
                            f"Local subtensor stuck at block {local_block}, waiting for {self.stuck_threshold} seconds."
                        )
                    elif asyncio.get_event_loop().time() - self.stuck_start_time > self.stuck_threshold:
                        self.report_to_discord(f"Local subtensor stuck at block {local_block}, resetting subtensor.")
                        # Subtensor is stuck for over a minute, trigger auto-healing
                        self.auto_heal_subtensor()
                        self.stuck_start_time = None
                else:
                    self.stuck_start_time = None

                self.previous_local_block = local_block
                self.previous_finney_block = finney_block
                self.previous_subvortex_block = subvortex_block

            await asyncio.sleep(12)

