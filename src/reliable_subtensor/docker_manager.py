"""
This module provides the `DockerManager` class, which facilitates the management
of Docker containers and volumes. It leverages the `docker` Python SDK to perform
operations such as retrieving, stopping, removing, and running containers, as well
as creating and managing Docker volumes.
"""

import logging
import time

import docker
from docker.errors import NotFound


class DockerManager:
    def __init__(self, container_name: str, volume_name: str):
        self.container_name = container_name
        self.volume_name = volume_name
        self.docker_client = docker.from_env()
        self.logger = logging.getLogger("DockerManager")

    def get_container(self) -> docker.models.containers.Container:
        try:
            return self.docker_client.containers.get(self.container_name)
        except docker.errors.NotFound as e:
            msg = f"Container '{self.container_name}' not found."
            self.logger.error(msg)
            raise Exception(msg) from e
        except Exception as e:
            self.logger.exception(f"Error accessing container '{self.container_name}': {e}")
            raise

    def get_volume(self) -> docker.models.volumes.Volume:
        try:
            return self.docker_client.volumes.get(self.volume_name)
        except docker.errors.NotFound as e:
            msg = f"Volume '{self.volume_name}' not found."
            self.logger.error(msg)
            raise Exception(msg) from e
        except Exception as e:
            self.logger.exception(f"Error accessing volume '{self.volume_name}': {e}")
            raise

    def stop_container(self, container: docker.models.containers.Container):
        try:
            container.stop()
            self.logger.info(f"Stopped container '{self.container_name}'.")
        except Exception as e:
            self.logger.exception(f"Error stopping container '{self.container_name}': {e}")
            raise

    def remove_container(self, container: docker.models.containers.Container):
        try:
            container.remove(force=True)
            self.logger.info(f"Removed container '{self.container_name}'.")
        except Exception as e:
            self.logger.exception(f"Error removing container '{self.container_name}': {e}")
            raise

    def remove_volume(self, volume: docker.models.volumes.Volume):
        try:
            volume.remove(force=True)
            self.logger.info(f"Removed volume '{self.volume_name}'.")
        except Exception as e:
            self.logger.exception(f"Error removing volume '{self.volume_name}': {e}")
            raise

    def create_volume(self):
        try:
            self.docker_client.volumes.create(name=self.volume_name)
            self.logger.info(f"Created volume '{self.volume_name}'.")
        except Exception as e:
            self.logger.exception(f"Error creating volume '{self.volume_name}': {e}")
            raise

    def wait_for_volume(self, timeout: int = 30, interval: int = 2):
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                self.docker_client.volumes.get(self.volume_name)
                self.logger.info(f"Volume '{self.volume_name}' is now available.")
                return
            except NotFound:
                self.logger.debug(f"Volume '{self.volume_name}' not found. Waiting...")
                time.sleep(interval)
            except Exception as e:
                self.logger.exception(f"Error while waiting for volume '{self.volume_name}': {e}")
                time.sleep(interval)
        self.logger.error(f"Timeout reached. Volume '{self.volume_name}' is not available after {timeout} seconds.")
        raise Exception(f"Volume '{self.volume_name}' is not available after {timeout} seconds.")

    def run_container(self, container: docker.models.containers.Container):
        try:
            ports = self._extract_ports(container)
            volumes = self._extract_volumes(container)

            image_tag = container.image.tags[0] if container.image.tags else None
            if not image_tag:
                msg = f"No image tag found for container '{self.container_name}'. Cannot restart container."
                self.logger.error(msg)
                raise Exception(msg)

            self.docker_client.containers.run(
                image=image_tag,
                name=self.container_name,
                detach=True,
                cpu_count=container.attrs["HostConfig"].get("CpuCount"),
                mem_limit=container.attrs["HostConfig"].get("Memory"),
                memswap_limit=container.attrs["HostConfig"].get("MemorySwap"),
                ports=ports,
                environment=container.attrs["Config"].get("Env"),
                volumes=volumes,
                command=container.attrs["Config"].get("Cmd"),
                network=container.attrs["HostConfig"].get("NetworkMode"),
            )
            self.logger.info(f"Restarted container '{self.container_name}' with image '{image_tag}'.")

        except Exception as e:
            self.logger.exception(f"Error restarting container '{self.container_name}': {e}")
            raise

    def _extract_ports(self, container: docker.models.containers.Container):
        port_bindings = container.attrs["HostConfig"].get("PortBindings", {})
        ports = {}
        for container_port, host_ports in port_bindings.items():
            host_port = int(host_ports[0]["HostPort"]) if host_ports else int(container_port.split("/")[0])
            ports[container_port] = host_port
        return ports

    def _extract_volumes(self, container: docker.models.containers.Container):
        volumes = {}
        for mount in container.attrs["Mounts"]:
            source = mount["Source"]
            target = mount["Destination"]
            volumes[source] = {"bind": target, "mode": mount.get("Mode", "rw")}
        return volumes
