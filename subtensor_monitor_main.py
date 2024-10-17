#!/usr/bin/env python

import asyncio
import os

from src.reliable_subtensor.subtensor_monitor import SubtensorMonitor


def main():
    discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL", None)
    subtensor_container_name = os.getenv("SUBTENSOR_CONTAINER_NAME", "subtensor-mainnet-lite")
    subtensor_volume_name = os.getenv("SUBTENSOR_VOLUME_NAME", None)
    local_url = os.getenv("LOCAL_URL", "ws://127.0.0.1:9944")
    finney_url = os.getenv("FINNEY_URL", "finney")
    subvortex_url = os.getenv("SUBVORTEX_URL", "ws://subvortex.info:9944")

    monitor = SubtensorMonitor(
        discord_webhook_url, subtensor_container_name, subtensor_volume_name, local_url, finney_url, subvortex_url
    )
    asyncio.run(monitor.monitor_subtensor())


if __name__ == "__main__":
    main()
