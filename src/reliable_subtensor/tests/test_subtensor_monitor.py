import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from reliable_subtensor.config import MonitorConfig as Config
from reliable_subtensor.subtensor_monitor import NodeStatus, SubtensorMonitor


class TestSubtensorMonitor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = Config(
            discord_webhook_url="http://discord.webhook.url",
            subtensor_container_name="subtensor-mainnet-lite",
            subtensor_volume_name="mainnet-lite-volume",
            local_url="ws://127.0.0.1:9944",
            finney_url="ws://finney",
            subvortex_url="ws://subvortex.info:9944",
            block_behind_threshold=2,
            stuck_threshold=60,
            syncing_threshold=300,
            external_check_interval=300,
        )
        self.monitor = SubtensorMonitor(self.config)
        self.monitor.logger = MagicMock()
        self.monitor.firewall = MagicMock()
        self.monitor.docker_manager = MagicMock()

    async def test_update_node_states_local_only(self):
        self.monitor.local_node.status = NodeStatus.CAUGHT_UP
        self.monitor.last_external_check_time = asyncio.get_event_loop().time() - (
            self.config.external_check_interval - 10
        )

        self.monitor.fetch_blocks = AsyncMock(return_value=[100])

        with patch.object(self.monitor.local_node, "update_block", new=MagicMock()) as mock_update_block:
            await self.monitor.update_node_states()

            mock_update_block.assert_called_once_with(100)
            self.assertEqual(mock_update_block.call_count, 1)

    async def test_update_node_states_all_nodes(self):
        self.monitor.local_node.status = NodeStatus.BEHIND
        self.monitor.last_external_check_time = asyncio.get_event_loop().time() - (
            self.config.external_check_interval + 10
        )

        self.monitor.fetch_blocks = AsyncMock(return_value=[100, 200, 300])

        with (
            patch.object(self.monitor.local_node, "update_block", new=MagicMock()) as mock_local_update_block,
            patch.object(self.monitor.finney_node, "update_block", new=MagicMock()) as mock_finney_update_block,
            patch.object(self.monitor.subvortex_node, "update_block", new=MagicMock()) as mock_subvortex_update_block,
        ):
            await self.monitor.update_node_states()

            mock_local_update_block.assert_called_once_with(100)
            mock_finney_update_block.assert_called_once_with(200)
            mock_subvortex_update_block.assert_called_once_with(300)

    async def test_determine_local_subtensor_status_caught_up(self):
        self.monitor.local_node.block_number = 100
        self.monitor.local_node.previous_block_number = 99
        self.monitor.is_syncing = AsyncMock(return_value=False)
        await self.monitor.determine_local_subtensor_status()
        self.assertEqual(self.monitor.local_node.status, NodeStatus.CAUGHT_UP)

    async def test_determine_local_subtensor_status_behind(self):
        self.monitor.local_node.block_number = 95
        self.monitor.local_node.previous_block_number = 95
        self.monitor.finney_node.block_number = 100
        self.monitor.subvortex_node.block_number = 100

        self.monitor.is_syncing = AsyncMock(return_value=False)
        self.monitor.fetch_blocks = AsyncMock(return_value=[100, 100])

        await self.monitor.determine_local_subtensor_status()
        self.assertEqual(self.monitor.local_node.status, NodeStatus.BEHIND)

    def test_auto_heal_subtensor(self):
        docker_manager = self.monitor.docker_manager
        self.monitor.auto_heal_subtensor()

        docker_manager.get_container.assert_called_once()
        docker_manager.get_volume.assert_called_once()
        docker_manager.stop_container.assert_called_once()
        docker_manager.remove_container.assert_called_once()
        docker_manager.remove_volume.assert_called_once()
        docker_manager.create_volume.assert_called_once()
        docker_manager.wait_for_volume.assert_called_once()
        docker_manager.run_container.assert_called_once()

    async def test_fetch_blocks_logging_on_failure(self):
        self.monitor.logger = logging.getLogger("SubtensorMonitor")
        self.monitor.logger.setLevel(logging.ERROR)

        with patch("reliable_subtensor.subtensor_monitor.subtensor") as mock_subtensor:
            mock_sub = MagicMock()
            type(mock_sub).block = PropertyMock(side_effect=Exception("Network Error"))
            mock_subtensor.return_value = mock_sub

            with patch("asyncio.to_thread", side_effect=Exception("Network Error")):
                with self.assertLogs("SubtensorMonitor", level="ERROR") as log:
                    block_numbers = await self.monitor.fetch_blocks([self.monitor.local_node])

                    self.assertEqual(block_numbers, [None])

                    self.assertTrue(any("Failed to fetch block number" in message for message in log.output))

    async def test_get_block_number_retry(self):
        with patch("reliable_subtensor.subtensor_monitor.subtensor") as mock_subtensor:
            mock_sub = MagicMock()
            type(mock_sub).block = PropertyMock(
                side_effect=[Exception("Temporary Error"), Exception("Temporary Error"), 100]
            )
            mock_subtensor.return_value = mock_sub

            block_number = await self.monitor.get_block_number(self.monitor.local_node)
            self.assertEqual(block_number, 100)

    async def test_firewall_applied_in_behind_state(self):
        self.monitor.local_node.status = NodeStatus.BEHIND
        self.monitor.is_firewalled = False

        self.monitor.finney_node.block_number = 100
        self.monitor.subvortex_node.block_number = 100
        self.monitor.local_node.block_number = 95

        self.monitor.log_and_report = MagicMock()

        await self.monitor.handle_behind_status()

        self.monitor.firewall.apply_firewall.assert_called_once()
        self.assertTrue(self.monitor.is_firewalled)
        expected_calls = [
            unittest.mock.call("Node is behind. Firewall applied.", log_level="warning"),
            unittest.mock.call("Node is behind by 5 blocks.", log_level="warning"),
        ]
        self.monitor.log_and_report.assert_has_calls(expected_calls)


if __name__ == "__main__":
    unittest.main()
