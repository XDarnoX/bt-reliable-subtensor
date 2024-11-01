"""
This module provides the `FirewallManager` class, which facilitates the management
of firewall rules using `iptables`. It allows users to apply and remove firewall
rules to control access to specific TCP ports, enhancing the security of Docker containers
or other services running on the host machine.
"""
import logging
import subprocess


class FirewallManager:
    def __init__(self, port: int = 9944):
        self.port = port
        self.logger = logging.getLogger("FirewallManager")

    def apply_firewall(self):
        self.logger.info(f"Applying firewall on port {self.port}")
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

    def remove_firewall(self):
        self.logger.info(f"Removing firewall on port {self.port}")
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
