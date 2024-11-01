"""
This module provides the `SubtensorMonitor` class, which is responsible for monitoring
Subtensor nodes, managing firewall rules, and performing auto-healing operations on the
Subtensor network. It interacts with Docker containers and volumes to ensure the Subtensor
node is running optimally and securely. Additionally, it sends notifications to Discord
channels to report the status and any issues encountered during monitoring.
"""

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

import aiohttp
from aiohttp import ClientConnectionError, ClientResponseError, ClientTimeout
from bittensor import subtensor
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.reliable_subtensor.config import MonitorConfig, configure_logging, load_and_validate_config
from src.reliable_subtensor.docker_manager import DockerManager
from src.reliable_subtensor.firewall_manager import FirewallManager


class MonitorState(Enum):
    SYNCING = auto()
    BEHIND = auto()
    CAUGHT_UP = auto()
    UNAVAILABLE = auto()


@dataclass
class NodeState:
    name: str
    url: str
    block_number: int | None = None
    previous_block_number: int | None = None
    last_update_time: float = field(default_factory=lambda: asyncio.get_event_loop().time())

    def update_block(self, new_block: int | None):
        self.previous_block_number = self.block_number
        self.block_number = new_block
        self.last_update_time = asyncio.get_event_loop().time()


class SubtensorMonitor:
    def __init__(self, config: MonitorConfig):
        self.config = config

        self.local_node = NodeState(name="Local", url=config.local_url)
        self.finney_node = NodeState(name="Finney", url=config.finney_url)
        self.subvortex_node = NodeState(name="Subvortex", url=config.subvortex_url)
        self.nodes = [self.local_node, self.finney_node, self.subvortex_node]

        self.monitor_state = MonitorState.CAUGHT_UP
        self.stuck_start_time: float | None = None
        self.syncing_start_time: float | None = None
        self.last_external_check_time: float = asyncio.get_event_loop().time()
        self.is_firewalled = False

        local_port = int(config.local_url.split(":")[-1])
        self.firewall = FirewallManager(port=local_port)
        self.docker_manager = DockerManager(config.subtensor_container_name, config.subtensor_volume_name)

        self.logger = logging.getLogger("SubtensorMonitor")

    async def monitor_subtensor(self):
        while True:
            await self.update_node_states()
            await self.determine_monitor_state()
            await self.handle_monitor_state()
            await asyncio.sleep(12)

    async def update_node_states(self):
        current_time = asyncio.get_event_loop().time()
        time_since_last_check = current_time - self.last_external_check_time

        if self.monitor_state == MonitorState.BEHIND or time_since_last_check >= self.config.external_check_interval:
            nodes_to_update = self.nodes
            self.last_external_check_time = current_time
            self.logger.debug("Performing external block comparison with Finney/Subvortex.")
        else:
            nodes_to_update = [self.local_node]
            self.logger.debug("Performing local block update only.")

        block_numbers = await self.fetch_blocks(nodes_to_update)
        for node, block_number in zip(nodes_to_update, block_numbers):
            node.update_block(block_number)
            self.logger.info(f"{node.name} block number updated to {block_number}")

    async def fetch_blocks(self, nodes: list[NodeState]) -> list[int | None]:
        tasks = [self.get_block_number(node) for node in nodes]
        return await asyncio.gather(*tasks)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )
    async def get_block_number(self, node: NodeState) -> int | None:
        try:
            block = await asyncio.wait_for(asyncio.to_thread(lambda: subtensor(network=node.url).block), timeout=10)
            return block
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching block from {node.name} subtensor after retries")
        except Exception as e:
            self.logger.exception(f"Error fetching block from {node.name} subtensor: {e}")
        return None

    async def determine_monitor_state(self):
        if self.local_node.block_number is None or self.local_node.previous_block_number is None:
            self.set_monitor_state(
                new_state=MonitorState.UNAVAILABLE,
                message="Local subtensor block number is None. Setting state to UNAVALIABLE.",
                log_level="error",
            )
            return
        
        try:
            is_syncing = await self.is_syncing()
        except Exception as e:
            self.set_monitor_state(
                new_state=MonitorState.UNAVAILABLE,
                message=f"Local subtensor node is unavailable due to syncing error: {e}. Setting state to UNAVAILABLE.",
                log_level="error",
            )
            return

        if is_syncing:
            self.set_monitor_state(
                new_state=MonitorState.SYNCING,
                message="Local subtensor node is syncing. Setting state to SYNCING.",
            )
            return

        block_increment = self.local_node.block_number - self.local_node.previous_block_number
        self.logger.debug(f"Local block increment: {block_increment}")

        if block_increment == 0:
            self.log_and_report("Local block has not incremented. Performing external check.", log_level="debug")
            new_monitor_state, log_level = await self.perform_external_check()
            if new_monitor_state is not None and new_monitor_state != self.monitor_state:
                self.set_monitor_state(
                    new_state=new_monitor_state,
                    message=f"Setting state to {new_monitor_state.name} after external nodes comparison.",
                    log_level=log_level,
                )
            return

        if self.monitor_state != MonitorState.CAUGHT_UP:
            self.set_monitor_state(
                new_state=MonitorState.CAUGHT_UP,
                message="Node has caught up. Setting state to CAUGHT_UP.",
            )

    def set_monitor_state(self, new_state: MonitorState, message: str, log_level: str = "info"):
        if self.monitor_state != new_state:
            self.monitor_state = new_state
            self.log_and_report(message, log_level)

    async def perform_external_check(self) -> tuple[Optional[MonitorState], str]:
        try:
            external_nodes = [self.finney_node, self.subvortex_node]
            block_numbers = await self.fetch_blocks(external_nodes)
            
            for node, block_number in zip(external_nodes, block_numbers):
                node.update_block(block_number)
                self.logger.info(f"{node.name} block number updated to {block_number}")

            external_blocks = [node.block_number for node in external_nodes if node.block_number is not None]
            if not external_blocks:
                self.logger.warning("Cannot determine if node is behind due to missing external block data.")
                return None, None

            max_external_block = max(external_blocks)
            block_difference = max_external_block - self.local_node.block_number
            self.logger.debug(f"Block difference after external check: {block_difference}")

            if block_difference >= self.config.block_behind_threshold:
                return MonitorState.BEHIND, "warning"

            return MonitorState.CAUGHT_UP, "info"
        except Exception as e:
            self.log_and_report(f"External check failed: {e}", log_level="exception")
            return None, None

    async def handle_monitor_state(self):
        if self.monitor_state == MonitorState.SYNCING:
            await self.handle_syncing_state()
        elif self.monitor_state == MonitorState.BEHIND:
            await self.handle_behind_state()
        elif self.monitor_state == MonitorState.CAUGHT_UP:
            await self.handle_caught_up_state()
        elif self.monitor_state == MonitorState.UNAVAILABLE:
            await self.handle_unavailable_state()

    async def handle_syncing_state(self):
        if not self.is_firewalled:
            self.firewall.apply_firewall()
            self.is_firewalled = True
            self.log_and_report("Node is syncing. Firewall applied.", log_level="info")

        if self.syncing_start_time is None:
            self.syncing_start_time = asyncio.get_event_loop().time()
            self.logger.info(f"Node started syncing at {self.syncing_start_time}.")
        else:
            elapsed_time = asyncio.get_event_loop().time() - self.syncing_start_time
            self.logger.debug(f"Node has been syncing for {elapsed_time} seconds.")
            if elapsed_time > self.config.syncing_threshold:
                self.log_and_report("Node has been syncing for too long. Initiating auto-heal.", log_level="error")
                try:
                    self.auto_heal_subtensor()
                    self.syncing_start_time = None
                except Exception:
                    self.log_and_report("Auto-heal failed during syncing state. Will retry in the next cycle.", log_level="exception")

    async def handle_behind_state(self):
        if not self.is_firewalled:
            self.firewall.apply_firewall()
            self.is_firewalled = True
            self.log_and_report("Node is behind. Firewall applied.", log_level="warning")

            external_blocks = list(filter(None, [self.finney_node.block_number, self.subvortex_node.block_number]))
            if external_blocks and self.local_node.block_number is not None:
                max_external_block = max(external_blocks)
                block_difference = max_external_block - self.local_node.block_number
                self.log_and_report(f"Node is behind by {block_difference} blocks.", log_level="warning")
            else:
                self.log_and_report(
                    "Unable to determine block difference due to missing block data.",
                    log_level="error",
                )

        self.syncing_start_time = None
        await self.check_for_stuck_node()

    async def handle_unavailable_state(self):
        if not self.is_firewalled:
            self.firewall.apply_firewall()
            self.is_firewalled = True
            self.log_and_report("Node is unavailable. Firewall applied.", log_level="error")
        await self.check_for_stuck_node()
        

    async def handle_caught_up_state(self):
        if self.is_firewalled:
            self.firewall.remove_firewall()
            self.is_firewalled = False
            self.log_and_report("Node has caught up. Firewall removed.")
        self.stuck_start_time = None

    async def check_for_stuck_node(self):
        is_stuck = self.local_node.block_number is None or self.local_node.block_number == self.local_node.previous_block_number
        if is_stuck:
            if self.stuck_start_time is None:
                self.stuck_start_time = asyncio.get_event_loop().time()
                self.logger.warning(f"Node appears stuck at block: {self.local_node.block_number}.")
            elif asyncio.get_event_loop().time() - self.stuck_start_time > self.config.stuck_threshold:
                self.log_and_report("Node is stuck. Initiating auto-heal.", log_level="error")
                try:
                    self.auto_heal_subtensor()
                    self.stuck_start_time = None
                except Exception:
                    self.logger.exception("Auto-heal failed. Will retry in the next cycle.")
        else:
            self.stuck_start_time = None

    def auto_heal_subtensor(self):
        try:
            container = self.docker_manager.get_container()
            volume = self.docker_manager.get_volume()

            self.docker_manager.stop_container(container)
            self.docker_manager.remove_container(container)
            self.docker_manager.remove_volume(volume)

            self.docker_manager.create_volume()
            self.docker_manager.wait_for_volume()
            self.docker_manager.run_container(container)

        except Exception as e:
            self.log_and_report(f"Auto-heal failed: {e}", log_level="exception")
            raise

    def log_and_report(self, message, log_level="info"):
        log_func = getattr(self.logger, log_level)
        log_func(message)
        asyncio.create_task(self._report_to_discord(message))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=False
    )
    async def _report_to_discord(self, message):
        try:
            ip_address = socket.gethostbyname(socket.gethostname())
            full_message = f"[{ip_address}] {message}"
            asyncio.create_task(self.send_discord_message(full_message))
        except Exception as e:
            self.logger.exception(f"Failed to send message to Discord: {e}")

    async def send_discord_message(self, message):
        try:
            async with aiohttp.ClientSession() as session:
                webhook_url = self.config.discord_webhook_url
                payload = {"content": message}
                async with session.post(webhook_url, json=payload, timeout=10) as response:
                    if response.status != 204:
                        raise ClientResponseError(response.request_info, response.history, status=response.status)
        except Exception as e:
            raise e

    async def is_syncing(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"jsonrpc": "2.0", "method": "system_health", "params": [], "id": 1}
                rpc_endpoint = self.local_node.url.replace("ws://", "http://").replace("wss://", "https://")

                timeout = ClientTimeout(total=10)
                async with session.post(rpc_endpoint, json=payload, timeout=timeout) as response:
                    response.raise_for_status()
                    result = await response.json()
                    is_syncing = result.get("result", {}).get("isSyncing", False)
                    return is_syncing
        except (ClientConnectionError, ClientResponseError, asyncio.TimeoutError) as e:
            self.logger.exception(f"Network-related error when checking syncing status: {e}")
            raise e
        except Exception as e:
            self.logger.exception(f"Exception when checking syncing status: {e}")
            return e


if __name__ == "__main__":
    logger = configure_logging()
    logger.info("SubtensorMonitor script is starting...")

    try:
        config = load_and_validate_config()
    except ValueError as e:
        logger.error(e)
        exit(1)

    monitor = SubtensorMonitor(config)
    try:
        asyncio.run(monitor.monitor_subtensor())
    except Exception:
        logger.exception("Exception occurred during SubtensorMonitor execution.")
