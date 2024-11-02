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

import aiohttp
from aiohttp import ClientConnectionError, ClientResponseError, ClientTimeout
from bittensor import subtensor
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from reliable_subtensor.config import MonitorConfig, configure_logging, load_and_validate_config
from reliable_subtensor.docker_manager import DockerManager
from reliable_subtensor.firewall_manager import FirewallManager


class NodeStatus(Enum):
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
    status: NodeStatus = NodeStatus.CAUGHT_UP

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

        self.stuck_start_time: float | None = None
        self.syncing_start_time: float | None = None
        self.last_external_check_time: float = asyncio.get_event_loop().time()
        self.is_firewalled = False

        local_port = int(config.local_url.split(":")[-1])
        self.firewall = FirewallManager(port=local_port)
        self.docker_manager = DockerManager(config.subtensor_container_name, config.subtensor_volume_name)

        self.logger = logging.getLogger("SubtensorMonitor")

    async def monitor_subtensor(self):
        """
        Monitors the status of the Subtensor node by continuously updating node states,
        determining the node's current status, and handling any necessary actions based
        on the status (such as firewall adjustments or auto-healing).

        This is the main loop that runs until the script is terminated.
        """
        while True:
            await self.update_node_states()
            await self.determine_local_subtensor_status()
            await self.handle_local_subtensor_status()
            await asyncio.sleep(12)

    async def update_node_states(self):
        """
        Updates the block numbers for each node (local and external) based on the current
        sync status. If the local node is behind or it's time for a regular external check,
        it fetches blocks for all nodes. Otherwise, it updates the local node only.

        Logs updates and performs an external block comparison if needed.
        """
        current_time = asyncio.get_event_loop().time()
        time_since_last_check = current_time - self.last_external_check_time

        if self.local_node.status == NodeStatus.BEHIND or time_since_last_check >= self.config.external_check_interval:
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

    async def determine_local_subtensor_status(self):
        """
        Determines the status of the local Subtensor node by evaluating whether the
        node is syncing, behind, caught up, or unavailable. Sets the local node's
        status and logs messages based on block increments, syncing status, or errors.

        Performs an external node comparison if necessary to adjust the status accordingly.
        """
        if self.local_node.block_number is None or self.local_node.previous_block_number is None:
            self.set_local_subtensor_status(
                new_state=NodeStatus.UNAVAILABLE,
                message="Local subtensor block number is None. Setting state to UNAVAILABLE.",
                log_level="error",
            )
            return

        try:
            is_syncing = await self.is_syncing()
        except Exception as e:
            self.set_local_subtensor_status(
                new_state=NodeStatus.UNAVAILABLE,
                message=f"Local subtensor node is unavailable due to syncing error: {e}. Setting state to UNAVAILABLE.",
                log_level="error",
            )
            return

        if is_syncing:
            self.set_local_subtensor_status(
                new_state=NodeStatus.SYNCING,
                message="Local subtensor node is syncing. Setting state to SYNCING.",
            )
            return

        block_increment = self.local_node.block_number - self.local_node.previous_block_number
        self.logger.debug(f"Local block increment: {block_increment}")

        if block_increment == 0:
            self.log_and_report("Local block has not incremented. Performing external check.", log_level="debug")
            new_monitor_state, log_level = await self.perform_external_check()
            if new_monitor_state is not None and new_monitor_state != self.local_node.status:
                self.set_local_subtensor_status(
                    new_state=new_monitor_state,
                    message=f"Setting state to {new_monitor_state.name} after external nodes comparison.",
                    log_level=log_level,
                )
            return

        if self.local_node.status != NodeStatus.CAUGHT_UP:
            self.set_local_subtensor_status(
                new_state=NodeStatus.CAUGHT_UP,
                message="Node has caught up. Setting state to CAUGHT_UP.",
            )

    async def fetch_blocks(self, nodes: list[NodeState]) -> list[int | None]:
        block_numbers = []
        for node in nodes:
            try:
                block_number = await self.get_block_number(node)
            except Exception as e:
                self.logger.error(f"Failed to fetch block number for {node.name} after retries: {e}")
                block_number = None
            block_numbers.append(block_number)
        return block_numbers

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )
    async def get_block_number(self, node: NodeState) -> int:
        block = await asyncio.wait_for(asyncio.to_thread(lambda: subtensor(network=node.url).block), timeout=10)
        return block

    async def perform_external_check(self) -> tuple[NodeStatus | None, str | None]:
        """
        Performs a check to determine if the local node is behind compared to external nodes.
        """
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
            block_difference = max_external_block - (self.local_node.block_number or 0)
            self.logger.debug(f"Block difference after external check: {block_difference}")

            if block_difference >= self.config.block_behind_threshold:
                return NodeStatus.BEHIND, "warning"

            return NodeStatus.CAUGHT_UP, "info"
        except Exception as e:
            self.log_and_report(f"External check failed: {e}", log_level="exception")
            return None, None

    async def handle_local_subtensor_status(self):
        if self.local_node.status == NodeStatus.SYNCING:
            await self.handle_syncing_status()
        elif self.local_node.status == NodeStatus.BEHIND:
            await self.handle_behind_status()
        elif self.local_node.status == NodeStatus.CAUGHT_UP:
            await self.handle_caught_up_status()
        elif self.local_node.status == NodeStatus.UNAVAILABLE:
            await self.handle_unavailable_status()

    async def handle_syncing_status(self):
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
                    self.log_and_report(
                        "Auto-heal failed during syncing state. Will retry in the next cycle.", log_level="exception"
                    )

    async def handle_behind_status(self):
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

    async def handle_unavailable_status(self):
        if not self.is_firewalled:
            self.firewall.apply_firewall()
            self.is_firewalled = True
            self.log_and_report("Node is unavailable. Firewall applied.", log_level="error")
        await self.check_for_stuck_node()

    async def handle_caught_up_status(self):
        if self.is_firewalled:
            self.firewall.remove_firewall()
            self.is_firewalled = False
            self.log_and_report("Node has caught up. Firewall removed.")
        self.stuck_start_time = None

    async def check_for_stuck_node(self):
        """
        Checks if the local node is stuck (i.e., not incrementing blocks). If the node has been
        stuck beyond a threshold, initiates auto-healing operations. Otherwise, resets the stuck timer.
        """
        is_stuck = (
            self.local_node.block_number is None
            or self.local_node.block_number == self.local_node.previous_block_number
        )
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
        """
        Attempts to auto-heal the local Subtensor node by restarting its Docker container and
        reinitializing the associated Docker volume. This is performed if the node is detected as stuck
        or failing to sync after retries.
        """
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

    async def is_syncing(self) -> bool | Exception:
        """
        Checks if the local Subtensor node is currently syncing by making an RPC call to the node's endpoint.
        """
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

    def set_local_subtensor_status(self, new_state: NodeStatus, message: str, log_level: str = "info"):
        if self.local_node.status != new_state:
            self.local_node.status = new_state
            self.log_and_report(message, log_level)

    def log_and_report(self, message, log_level="info"):
        """
        Logs a message and sends it as a notification to Discord. Creates an asynchronous task
        to send the message.
        """
        log_func = getattr(self.logger, log_level)
        log_func(message)
        asyncio.create_task(self._report_to_discord(message))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=False,
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
