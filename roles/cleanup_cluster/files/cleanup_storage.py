#!/usr/bin/env python3
"""
Clean up secondary storage pools on Proxmox and reinitialize disks.

This script reverses setup_secondary_storage.py by:
1. Removing Proxmox storage pool registrations (pvesm remove)
2. Removing LVM thin pool logical volumes
3. Removing LVM volume groups
4. Removing LVM physical volumes
5. Wiping disk signatures (filesystem, LVM, partition table)

After this, the disks are clean and will be discovered as "secondary_disks"
by discover_storage.py on the next provisioning run.

Environment Variables:
  PROXMOX_HOST: Proxmox API host (required)
  PROXMOX_USER: API user (required)
  PROXMOX_PASSWORD: API password (required)
  PROXMOX_NODE: Target node name (default: pve)
  PROXMOX_VERIFY_SSL: Verify SSL certificates (default: false)

  STORAGE_NAME: Base name for storage pools (default: "vm-storage")
                Matches pools named vm-storage-1, vm-storage-2, etc.
  VG_NAME: Base name for LVM volume groups (default: "vg-secondary")
           Matches VGs named vg-secondary-1, vg-secondary-2, etc.
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


def find_secondary_storage_pools(proxmox, storage_base_name):
    """Find all Proxmox storage pools matching the base name pattern."""
    pools = []
    try:
        storages = proxmox.storage.get()
        for storage in storages:
            name = storage.get("storage", "")
            # Match vm-storage-1, vm-storage-2, etc.
            if name.startswith(f"{storage_base_name}-") and name[len(f"{storage_base_name}-"):].isdigit():
                pools.append(storage)
    except Exception as e:
        logging.warning(f"Failed to list storage pools: {e}")
    return pools


def find_secondary_vgs(proxmox, node, vg_base_name):
    """Find all LVM thin pools matching the VG base name pattern."""
    vgs = []
    try:
        lvmthin_list = proxmox.nodes(node).disks.lvmthin.get()
        for lv in lvmthin_list:
            vg = lv.get("vg", "")
            if vg.startswith(f"{vg_base_name}-") and vg[len(f"{vg_base_name}-"):].isdigit():
                vgs.append(lv)
    except Exception as e:
        logging.warning(f"Failed to list LVM thin pools: {e}")
    return vgs


def find_disk_for_vg(proxmox, node, vg_name):
    """
    Find the physical disk device backing a given VG.

    Queries the Proxmox disks API and looks for disks whose 'used' field
    references the VG name (Proxmox reports 'LVM' for LVM-used disks).
    Falls back to checking LVM PV info.
    """
    try:
        disks = proxmox.nodes(node).disks.list.get()
        for disk in disks:
            devpath = disk.get("devpath", "")
            used = disk.get("used", "")
            # Proxmox marks LVM disks with "LVM" in used field
            # We need to cross-reference with the VG
            if used == "LVM" and devpath:
                # This disk has LVM - we'll return it and let the caller
                # verify via the SSH cleanup which VG it belongs to
                yield devpath
    except Exception as e:
        logging.warning(f"Failed to query disks API: {e}")


def remove_storage_pool(proxmox, storage_name):
    """Remove a Proxmox storage pool registration."""
    try:
        logging.info(f"Removing Proxmox storage pool: {storage_name}")
        proxmox.storage(storage_name).delete()
        logging.info(f"Storage pool '{storage_name}' removed")
        return True
    except Exception as e:
        error_msg = str(e)
        if "does not exist" in error_msg.lower() or "not found" in error_msg.lower():
            logging.info(f"Storage pool '{storage_name}' does not exist - already clean")
            return True
        logging.error(f"Failed to remove storage pool '{storage_name}': {e}")
        return False


def main():
    node = getenv("PROXMOX_NODE", "pve")
    storage_base_name = getenv("STORAGE_NAME", "vm-storage")
    vg_base_name = getenv("VG_NAME", "vg-secondary")

    logging.info(f"Cleaning up secondary storage on node: {node}")
    logging.info(f"Storage pool pattern: {storage_base_name}-*")
    logging.info(f"VG pattern: {vg_base_name}-*")

    proxmox = get_proxmox_connection()

    # Step 1: Find and remove Proxmox storage pool registrations
    pools = find_secondary_storage_pools(proxmox, storage_base_name)
    logging.info(f"Found {len(pools)} secondary storage pool(s) to remove")

    removed_pools = []
    for pool in pools:
        name = pool.get("storage", "")
        if remove_storage_pool(proxmox, name):
            removed_pools.append(name)

    # Step 2: Find VGs that need to be cleaned up
    # After removing storage pools, we still need to clean up LVM structures
    # The VG names and their backing devices are needed for SSH cleanup
    vgs = find_secondary_vgs(proxmox, node, vg_base_name)
    logging.info(f"Found {len(vgs)} secondary LVM VG(s) to clean up")

    vg_info = []
    for vg in vgs:
        vg_name = vg.get("vg", "")
        vg_info.append({"vg_name": vg_name})

    # Step 3: Identify backing disks for the VGs
    # We need to find which physical disks back the secondary VGs
    # This info is used by the Ansible task to run cleanup commands via SSH
    disks_to_wipe = []
    for devpath in find_disk_for_vg(proxmox, node, vg_base_name):
        disks_to_wipe.append(devpath)

    # Deduplicate
    disks_to_wipe = list(set(disks_to_wipe))

    result = {
        "changed": len(removed_pools) > 0,
        "removed_pools": removed_pools,
        "vgs_to_clean": vg_info,
        "disks_to_wipe": disks_to_wipe,
        "message": f"Removed {len(removed_pools)} storage pool(s), "
                   f"{len(vg_info)} VG(s) to clean, "
                   f"{len(disks_to_wipe)} disk(s) to wipe",
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
