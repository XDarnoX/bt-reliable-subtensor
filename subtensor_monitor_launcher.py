"""
This module provides functionality to automatically monitor and restart the `SubtensorMonitor` 
process whenever changes are detected in the `subtensor_monitor.py` file. It utilizes the 
`watchdog` library to observe file system events and manages the `SubtensorMonitor` process 
using Python's `subprocess` module.
"""

import os
import subprocess
import sys
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from src.reliable_subtensor.config import configure_logging

class ReloadHandler(FileSystemEventHandler):
    def __init__(self, logger):
        super().__init__()
        self.process = None
        self.logger = logger
        self.start_monitor()

    def start_monitor(self):
        if self.process:
            self.logger.info("Terminating existing SubtensorMonitor process...")
            self.process.terminate()
            self.process.wait()
            self.logger.info("Existing SubtensorMonitor process terminated.")
        
        self.logger.info("Starting SubtensorMonitor...")
        self.process = subprocess.Popen(
            [sys.executable, "src/reliable_subtensor/subtensor_monitor.py"],
            env=os.environ,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

    def on_modified(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith("subtensor_monitor.py"):
            self.logger.info(f"Change detected in {event.src_path}, restarting SubtensorMonitor...")
            self.start_monitor()

def main():
    configure_logging()
    logger = logging.getLogger(__name__)

    event_handler = ReloadHandler(logger)
    
    observer = Observer()
    observer.schedule(event_handler, path='src/reliable_subtensor', recursive=False)
    observer.start()
    logger.info("Watching for changes to subtensor_monitor.py...")

    try:
        while True:
            observer.join(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Stopping observer...")
        observer.stop()
    except Exception as e:
        logger.exception(f"An unexpected error occurred in monitor_launcher: {e}")
    finally:
        observer.stop()
        observer.join()
        logger.info("Observer stopped.")

if __name__ == "__main__":
    main()
