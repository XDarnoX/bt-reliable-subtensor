# Reliable Subtensor

## Overview

**Reliable Subtensor** is a robust and automated monitoring solution designed to ensure the optimal performance and security of Subtensor nodes. This tool provides comprehensive oversight and automated recovery mechanisms to maintain the health and integrity of the Subtensor node.

## Features

### 1. **Node State Detection**

Subtensor Monitor continuously monitors the state of your Subtensor node by tracking block numbers across local and external nodes. It intelligently determines the synchronization status of your node through the following states:

- **SYNCING:** The node is actively synchronizing with the network.
- **BEHIND:** The node has fallen behind the latest blocks.
- **CAUGHT_UP:** The node is fully synchronized with the network.
- **UNAVAILABLE:** The node is unreachable or not responding, indicating potential downtime or connectivity issues.

**Logic Behind State Detection:**

- **Block Increment Tracking:** By comparing the current and previous block numbers, the monitor detects whether the node is making progress.
- **External Comparisons:** Cross-references block numbers with external nodes (Finney and Subvortex) to assess if the local node is behind.
- **Timeout Mechanisms:** Implements thresholds to identify prolonged syncing or stuck states, triggering appropriate actions.

### 2. **Discord Bot Notifications**

Stay informed in real-time with automated Discord notifications that alert you to critical events and state changes:

- **State Changes:** Receive updates when the node transitions between SYNCING, BEHIND, CAUGHT_UP and UNAVAILABLE states.
- **Error Alerts:** Get notified of any errors encountered during monitoring, firewall management, or auto-healing processes.
- **Recovery Actions:** Alerts when auto-healing procedures are initiated or successfully completed.

### 3. **Auto-Healing Mechanisms**

Subtensor Monitor doesn't just detect issues — it actively resolves them through automated healing processes:

- **Docker Container Management:**
  - **Auto-Restarting Unhealthy Node** When the local Subtensor node is detected as unhealthy and remains in this state beyond a predefined threshold time, the monitor will gracefully stop and forcefully remove the Subtensor Docker container.
  - **Volume Management:** Along with addressing the containers, the system removes and recreates Docker volumes to ensure a clean and stable environment.

- **Firewall Management:**
  Automatically applies or removes firewall rules based on the node's state to enhance security and access control.
