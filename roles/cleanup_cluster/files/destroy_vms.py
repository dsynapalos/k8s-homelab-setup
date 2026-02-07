#!/usr/bin/env python3
"""
Destroy VMs on Proxmox that match names from the cluster inventory.

This script:
1. Lists all VMs on the target Proxmox node
2. Matches VMs by name against the provided comma-separated list
3. Stops each matched VM (if running)
4. Destroys each matched VM (including disks)

Environment Variables:
  PROXMOX_HOST: Proxmox API host (required)
  PROXMOX_USER: API user (required for password auth)
  PROXMOX_PASSWORD: API password (required for password auth)
  PROXMOX_TOKEN_NAME: API token name (alternative to password auth)
  PROXMOX_TOKEN_VALUE: API token value (alternative to password auth)
  PROXMOX_NODE: Target node name (default: pve)
  PROXMOX_VERIFY_SSL: Verify SSL certificates (default: false)

  VM_NAMES: Comma-separated list of VM names to destroy (required)
            e.g., "k8s-control-1,k8s-node-1"
"""

import os
import sys
import json
import time
import logging
from proxmoxer import ProxmoxAPI

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TASK_TIMEOUT = 120
TASK_POLL_INTERVAL = 2


def getenv(key, default=None):
    val = os.environ.get(key)
    return val if val is not None else default


def getenv_bool(key, default=False):
    val = getenv(key, None)
    if val is None:
        return bool(default)
    return val.lower() in ("1", "true", "yes", "on")


def get_proxmox_connection():
    """Establish connection to Proxmox API."""
    host = getenv("PROXMOX_HOST")
    if not host:
        logging.error("PROXMOX_HOST must be set")
        sys.exit(1)

    token_name = getenv("PROXMOX_TOKEN_NAME")
    token_value = getenv("PROXMOX_TOKEN_VALUE")
    user = getenv("PROXMOX_USER")
    password = getenv("PROXMOX_PASSWORD", getenv("PROXMOX_PASS"))
    verify_ssl = getenv_bool("PROXMOX_VERIFY_SSL", False)

    try:
        if token_name and token_value:
            return ProxmoxAPI(host, token_name=token_name, token_value=token_value, verify_ssl=verify_ssl)
        else:
            if not user or not password:
                logging.error("PROXMOX_USER and PROXMOX_PASSWORD must be set")
                sys.exit(1)
            return ProxmoxAPI(host, user=user, password=password, verify_ssl=verify_ssl)
    except Exception as e:
        logging.error(f"Failed to connect to Proxmox API: {e}")
        sys.exit(1)


def wait_for_task(proxmox, node, upid, timeout=TASK_TIMEOUT):
    """Wait for an async Proxmox task (UPID) to complete."""
    logging.info(f"Waiting for task: {upid}")
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            status = proxmox.nodes(node).tasks(upid).status.get()
            task_status = status.get("status", "")

            if task_status == "stopped":
                exitstatus = status.get("exitstatus", "")
                if exitstatus == "OK":
                    logging.info("Task completed successfully")
                    return True
                else:
                    logging.error(f"Task failed with status: {exitstatus}")
                    return False

            time.sleep(TASK_POLL_INTERVAL)
        except Exception as e:
            logging.warning(f"Error checking task status: {e}")
            time.sleep(TASK_POLL_INTERVAL)

    logging.error(f"Task timed out after {timeout} seconds")
    return False


def stop_vm(proxmox, node, vmid):
    """Stop a VM and wait for it to be stopped."""
    try:
        status = proxmox.nodes(node).qemu(vmid).status.current.get()
        if status.get("status") == "stopped":
            logging.info(f"VM {vmid} is already stopped")
            return True

        logging.info(f"Stopping VM {vmid}...")
        upid = proxmox.nodes(node).qemu(vmid).status.stop.post()
        return wait_for_task(proxmox, node, upid)
    except Exception as e:
        logging.error(f"Failed to stop VM {vmid}: {e}")
        return False


def destroy_vm(proxmox, node, vmid):
    """Destroy a VM including its disks."""
    try:
        logging.info(f"Destroying VM {vmid} (including disks)...")
        upid = proxmox.nodes(node).qemu(vmid).delete(purge=1, **{"destroy-unreferenced-disks": 1})
        return wait_for_task(proxmox, node, upid)
    except Exception as e:
        logging.error(f"Failed to destroy VM {vmid}: {e}")
        return False


def main():
    node = getenv("PROXMOX_NODE", "pve")
    vm_names_raw = getenv("VM_NAMES", "")

    if not vm_names_raw:
        logging.error("VM_NAMES must be set (comma-separated VM names)")
        sys.exit(1)

    vm_names = [n.strip() for n in vm_names_raw.split(",") if n.strip()]
    if not vm_names:
        logging.error("No valid VM names provided in VM_NAMES")
        sys.exit(1)

    logging.info(f"Target VMs for destruction: {vm_names}")

    proxmox = get_proxmox_connection()

    # List all VMs on the node
    try:
        existing_vms = proxmox.nodes(node).qemu.get()
    except Exception as e:
        logging.error(f"Failed to list VMs on node {node}: {e}")
        sys.exit(1)

    # Build a map of name -> vmid for target VMs
    targets = {}
    for vm in existing_vms:
        name = vm.get("name", "")
        vmid = vm.get("vmid")
        if name in vm_names:
            targets[name] = vmid

    # Report VMs not found
    for name in vm_names:
        if name not in targets:
            logging.info(f"VM '{name}' not found on node {node} - skipping")

    if not targets:
        logging.info("No matching VMs found - nothing to destroy")
        result = {"changed": False, "destroyed": [], "message": "No matching VMs found"}
        print(json.dumps(result))
        return 0

    destroyed = []
    failed = []

    for name, vmid in targets.items():
        logging.info(f"Processing VM: {name} (vmid={vmid})")

        # Stop the VM first
        if not stop_vm(proxmox, node, vmid):
            logging.error(f"Failed to stop VM {name} ({vmid}) - attempting force destroy")

        # Destroy the VM
        if destroy_vm(proxmox, node, vmid):
            destroyed.append({"name": name, "vmid": vmid})
            logging.info(f"Successfully destroyed VM {name} ({vmid})")
        else:
            failed.append({"name": name, "vmid": vmid})
            logging.error(f"Failed to destroy VM {name} ({vmid})")

    if failed:
        logging.error(f"Failed to destroy {len(failed)} VM(s): {[f['name'] for f in failed]}")
        sys.exit(1)

    result = {
        "changed": len(destroyed) > 0,
        "destroyed": destroyed,
        "message": f"Destroyed {len(destroyed)} VM(s)",
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
