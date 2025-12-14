#!/usr/bin/env python3
"""
Setup secondary storage pools on Proxmox for VM disk provisioning.

This script:
1. Creates an LVM thin pool for EACH available secondary disk
2. Registers each thin pool as Proxmox storage

This enables splitting physical disk capacity across multiple VMs.

Storage naming convention:
- First disk: vm-storage-1 (VG: vg-secondary-1)
- Second disk: vm-storage-2 (VG: vg-secondary-2)
- etc.

Environment Variables:
  PROXMOX_HOST: Proxmox API host (required)
  PROXMOX_USER: API user (required)
  PROXMOX_PASSWORD: API password (required)
  PROXMOX_NODE: Target node name (default: pve)
  PROXMOX_VERIFY_SSL: Verify SSL certificates (default: false)
  
  SECONDARY_DISKS: Comma-separated list of disk device paths (required)
                   e.g., "/dev/nvme0n1,/dev/sda"
  STORAGE_NAME: Base name for storage pools (default: "vm-storage")
                Will be suffixed with -1, -2, etc.
  VG_NAME: Base name for LVM volume groups (default: "vg-secondary")
           Will be suffixed with -1, -2, etc.
"""

import os
import sys
import json
import time
import logging
from proxmoxer import ProxmoxAPI

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Timeout for waiting on async tasks (seconds)
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
    """
    Wait for an async Proxmox task (UPID) to complete.
    
    Args:
        proxmox: ProxmoxAPI connection
        node: Node name
        upid: Task UPID string
        timeout: Maximum seconds to wait
    
    Returns:
        True if task succeeded, False otherwise
    """
    logging.info(f"Waiting for task to complete: {upid}")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            status = proxmox.nodes(node).tasks(upid).status.get()
            task_status = status.get('status', '')
            
            if task_status == 'stopped':
                # Task finished - check if it succeeded
                exitstatus = status.get('exitstatus', '')
                if exitstatus == 'OK':
                    logging.info(f"Task completed successfully")
                    return True
                else:
                    logging.error(f"Task failed with status: {exitstatus}")
                    return False
            
            # Task still running
            logging.debug(f"Task still running: {task_status}")
            time.sleep(TASK_POLL_INTERVAL)
            
        except Exception as e:
            logging.warning(f"Error checking task status: {e}")
            time.sleep(TASK_POLL_INTERVAL)
    
    logging.error(f"Task timed out after {timeout} seconds")
    return False


def check_storage_exists(proxmox, storage_name):
    """Check if a storage pool with the given name already exists."""
    try:
        storages = proxmox.storage.get()
        for storage in storages:
            if storage.get('storage') == storage_name:
                return True
        return False
    except Exception as e:
        logging.warning(f"Failed to check existing storage: {e}")
        return False


def check_lvmthin_exists(proxmox, node, vg_name):
    """Check if an LVM thin pool already exists for the given VG."""
    try:
        lvmthin_list = proxmox.nodes(node).disks.lvmthin.get()
        for lv in lvmthin_list:
            if lv.get('vg') == vg_name:
                return lv
        return None
    except Exception as e:
        logging.warning(f"Failed to check existing LVM thin pools: {e}")
        return None


def create_lvm_thinpool(proxmox, node, vg_name, device_path):
    """
    Create an LVM thin pool on a single device.
    
    Uses Proxmox API: POST /nodes/{node}/disks/lvmthin
    
    Note: This API creates:
    1. Physical volume on the device
    2. A volume group with name = vg_name
    3. A thin pool logical volume with name = vg_name (same as VG)
    
    The API is async and returns a UPID. We wait for completion.
    """
    params = {
        "device": device_path,
        "name": vg_name,
        "add_storage": 0,  # We'll add storage separately for more control
    }
    
    logging.info(f"Creating LVM thin pool: VG={vg_name}, device={device_path}")
    
    try:
        upid = proxmox.nodes(node).disks.lvmthin.post(**params)
        logging.info(f"LVM thin pool creation initiated: {upid}")
        
        # Wait for the async task to complete
        if not wait_for_task(proxmox, node, upid):
            logging.error(f"LVM thin pool creation task failed")
            return False
        
        return True
    except Exception as e:
        error_msg = str(e)
        if "already" in error_msg.lower() or "exists" in error_msg.lower():
            logging.info(f"LVM thin pool {vg_name} already exists")
            return True
        logging.error(f"Failed to create LVM thin pool: {e}")
        return False


def add_storage_pool(proxmox, storage_name, vg_name, thinpool_name=None):
    """
    Register the LVM thin pool as a Proxmox storage.
    
    Uses Proxmox API: POST /storage
    
    Note: When Proxmox creates an LVM thin pool via the API, the thinpool
    logical volume has the same name as the VG (not "data" like the default).
    """
    # Proxmox API creates thinpool with same name as VG
    if thinpool_name is None:
        thinpool_name = vg_name
    
    params = {
        "storage": storage_name,
        "type": "lvmthin",
        "vgname": vg_name,
        "thinpool": thinpool_name,
        "content": "images,rootdir",  # Allow VM disks and containers
        "nodes": getenv("PROXMOX_NODE", "pve"),  # Restrict to specific node
    }
    
    logging.info(f"Registering storage pool: {storage_name} (VG={vg_name}, thinpool={thinpool_name})")
    
    try:
        result = proxmox.storage.post(**params)
        logging.info(f"Storage pool registered: {result}")
        return True
    except Exception as e:
        error_msg = str(e)
        if "already exists" in error_msg.lower():
            logging.info(f"Storage pool {storage_name} already exists")
            return True
        logging.error(f"Failed to register storage pool: {e}")
        return False


def main():
    node = getenv("PROXMOX_NODE", "pve")
    storage_base_name = getenv("STORAGE_NAME", "vm-storage")
    vg_base_name = getenv("VG_NAME", "vg-secondary")
    secondary_disks_raw = getenv("SECONDARY_DISKS", "")
    
    if not secondary_disks_raw:
        logging.error("SECONDARY_DISKS must be set (comma-separated device paths)")
        sys.exit(1)
    
    device_paths = [d.strip() for d in secondary_disks_raw.split(",") if d.strip()]
    
    if not device_paths:
        logging.error("No valid device paths provided in SECONDARY_DISKS")
        sys.exit(1)
    
    logging.info(f"Setting up secondary storage on node: {node}")
    logging.info(f"Devices: {device_paths}")
    
    proxmox = get_proxmox_connection()
    
    created_storages = []
    all_existed = True
    
    # Create one storage pool per disk
    for idx, device_path in enumerate(device_paths, start=1):
        storage_name = f"{storage_base_name}-{idx}"
        vg_name = f"{vg_base_name}-{idx}"
        
        logging.info(f"Processing disk {idx}: {device_path} -> {storage_name}")
        
        # Check if this storage already exists
        if check_storage_exists(proxmox, storage_name):
            logging.info(f"Storage pool '{storage_name}' already exists - skipping")
            created_storages.append({
                "storage_name": storage_name,
                "vg_name": vg_name,
                "device": device_path,
                "existed": True,
            })
            continue
        
        all_existed = False
        
        # Check if LVM thin pool exists for this VG
        existing_lvm = check_lvmthin_exists(proxmox, node, vg_name)
        
        if not existing_lvm:
            # Create LVM thin pool for this single device
            if not create_lvm_thinpool(proxmox, node, vg_name, device_path):
                logging.error(f"Failed to create thin pool for {device_path}")
                sys.exit(1)
        else:
            logging.info(f"LVM VG '{vg_name}' already exists")
        
        # Register as Proxmox storage
        if not add_storage_pool(proxmox, storage_name, vg_name):
            logging.error(f"Failed to register storage pool {storage_name}")
            sys.exit(1)
        
        created_storages.append({
            "storage_name": storage_name,
            "vg_name": vg_name,
            "device": device_path,
            "existed": False,
        })
    
    result = {
        "changed": not all_existed,
        "storages": created_storages,
        "message": f"Created {len(created_storages)} storage pools" if not all_existed else "All storage pools already existed",
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
