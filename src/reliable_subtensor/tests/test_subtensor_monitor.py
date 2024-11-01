import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from aioresponses import aioresponses

from ..config import MonitorConfig as Config
from ..subtensor_monitor import MonitorState, SubtensorMonitor


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
        self.monitor.monitor_state = MonitorState.CAUGHT_UP
        self.monitor.last_external_check_time = asyncio.get_event_loop().time() - (
            self.config.external_check_interval - 10
        )

        self.monitor.fetch_blocks = AsyncMock(return_value=[100])

        with patch.object(self.monitor.local_node, "update_block", new=AsyncMock()) as mock_local_update_block:
            await self.monitor.update_node_states()

            expected_calls = [call(100)]

            mock_local_update_block.assert_has_calls(expected_calls, any_order=False)
            self.assertEqual(mock_local_update_block.call_count, 1)

    async def test_update_node_states_all_nodes(self):
        self.monitor.monitor_state = MonitorState.BEHIND
        self.monitor.last_external_check_time = asyncio.get_event_loop().time() - (
            self.config.external_check_interval + 10
        )

        self.monitor.fetch_blocks = AsyncMock(return_value=[100, 200, 300])

        with (
            patch.object(self.monitor.local_node, "update_block", new=AsyncMock()) as mock_local_update_block,
            patch.object(self.monitor.finney_node, "update_block", new=AsyncMock()) as mock_finney_update_block,
            patch.object(self.monitor.subvortex_node, "update_block", new=AsyncMock()) as mock_subvortex_update_block,
        ):
            await self.monitor.update_node_states()

            mock_local_update_block.assert_called_once_with(100)
            mock_finney_update_block.assert_called_once_with(200)
            mock_subvortex_update_block.assert_called_once_with(300)

    async def test_determine_monitor_state_caught_up(self):
        self.monitor.local_node.block_number = 100
        self.monitor.local_node.previous_block_number = 99

        await self.monitor.determine_monitor_state()
        self.assertEqual(self.monitor.monitor_state, MonitorState.CAUGHT_UP)

    async def test_determine_monitor_state_behind(self):
        self.monitor.local_node.block_number = 95
        self.monitor.local_node.previous_block_number = 95
        self.monitor.finney_node.block_number = 100
        self.monitor.subvortex_node.block_number = 100
        self.monitor.fetch_blocks = AsyncMock(return_value=[100, 100])

        await self.monitor.determine_monitor_state()
        self.assertEqual(self.monitor.monitor_state, MonitorState.BEHIND)

    async def test_handle_monitor_state_caught_up(self):
        self.monitor.monitor_state = MonitorState.CAUGHT_UP
        self.monitor.is_firewalled = True

        with patch.object(self.monitor, "handle_caught_up_state", new=AsyncMock()) as mock_handler:
            await self.monitor.handle_monitor_state()
            mock_handler.assert_called_once()

    async def test_handle_monitor_state_behind(self):
        self.monitor.monitor_state = MonitorState.BEHIND
        self.monitor.is_firewalled = False

        with patch.object(self.monitor, "handle_behind_state", new=AsyncMock()) as mock_handler:
            await self.monitor.handle_monitor_state()
            mock_handler.assert_called_once()

    async def test_handle_monitor_state_syncing(self):
        self.monitor.monitor_state = MonitorState.SYNCING
        self.monitor.is_firewalled = False

        with patch.object(self.monitor, "handle_syncing_state", new=AsyncMock()) as mock_handler:
            await self.monitor.handle_monitor_state()
            mock_handler.assert_called_once()

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

    async def test_get_block_number_exception(self):
        self.monitor.fetch_block = AsyncMock(side_effect=Exception("Network Error"))
        self.monitor.report_to_discord = MagicMock()

        block_number = await self.monitor.get_block_number(self.monitor.local_node)
        self.assertIsNone(block_number)
        self.monitor.report_to_discord.assert_called_once_with("Error fetching block from Local: Network Error")

    async def test_fetch_block_retry(self):
        call_count = 0

        async def mock_fetch_block(node):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary Error")
            return 100

        with patch("asyncio.to_thread", side_effect=mock_fetch_block):
            block_number = await self.monitor.fetch_block(self.monitor.local_node)
            self.assertEqual(block_number, 100)
            self.assertEqual(call_count, 3)

    async def test_firewall_applied_in_behind_state(self):
        self.monitor.monitor_state = MonitorState.BEHIND
        self.monitor.is_firewalled = False

        self.monitor.finney_node.block_number = 100
        self.monitor.subvortex_node.block_number = 100
        self.monitor.local_node.block_number = 95

        self.monitor.report_to_discord = MagicMock()

        await self.monitor.handle_behind_state()

        self.monitor.firewall.apply_firewall.assert_called_once()
        self.assertTrue(self.monitor.is_firewalled)

        self.monitor.report_to_discord.assert_called_once_with("Node is behind by 5 blocks. Firewall applied.")

    @aioresponses()
    async def test_is_syncing_method_true(self, mocked):
        rpc_endpoint = self.monitor.local_node.url.replace("ws://", "http://").replace("wss://", "https://")
        response_json = {"jsonrpc": "2.0", "result": {"isSyncing": True}, "id": 1}

        mocked.post(rpc_endpoint, payload=response_json)

        is_syncing = await self.monitor.is_syncing()
        self.assertTrue(is_syncing)

    @aioresponses()
    async def test_is_syncing_method_false(self, mocked):
        rpc_endpoint = self.monitor.local_node.url.replace("ws://", "http://").replace("wss://", "https://")
        response_json = {"jsonrpc": "2.0", "result": {"isSyncing": False}, "id": 1}

        mocked.post(rpc_endpoint, payload=response_json)

        is_syncing = await self.monitor.is_syncing()
        self.assertFalse(is_syncing)


if __name__ == "__main__":
    unittest.main()
