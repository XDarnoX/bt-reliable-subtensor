"""
This module provides configuration and logging setup functionalities for the SubtensorMonitor application. It defines the `MonitorConfig` data class for holding configuration parameters, along with utility functions to configure the logging system and load and validate environment-based configurations.
"""

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class MonitorConfig:
    discord_webhook_url: str
    subtensor_container_name: str
    subtensor_volume_name: str
    local_url: str
    finney_url: str
    subvortex_url: str
    block_behind_threshold: int
    stuck_threshold: int
    syncing_threshold: int
    external_check_interval: int


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("SubtensorMonitor")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def load_and_validate_config() -> MonitorConfig:
    load_dotenv()

    required_settings = [
        "DISCORD_WEBHOOK_URL",
    ]

    missing_settings = [setting for setting in required_settings if not os.getenv(setting)]
    if missing_settings:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_settings)}")

    return MonitorConfig(
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        subtensor_container_name=os.getenv("SUBTENSOR_CONTAINER_NAME", "subtensor-mainnet-lite"),
        subtensor_volume_name=os.getenv("SUBTENSOR_VOLUME_NAME", "mainnet-lite-volume"),
        local_url=os.getenv("LOCAL_URL", "ws://127.0.0.1:9944"),
        finney_url=os.getenv("FINNEY_URL", "finney"),
        subvortex_url=os.getenv("SUBVORTEX_URL", "ws://subvortex.info:9944"),
        block_behind_threshold=int(os.getenv("BLOCK_BEHIND_THRESHOLD", "2")),
        stuck_threshold=int(os.getenv("STUCK_THRESHOLD", "60")),
        syncing_threshold=int(os.getenv("SYNCING_THRESHOLD", "300")),
        external_check_interval=int(os.getenv("EXTERNAL_CHECK_INTERVAL", "300")),
    )
