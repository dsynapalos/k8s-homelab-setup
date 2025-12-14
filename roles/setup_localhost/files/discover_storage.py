#!/usr/bin/env python3
"""
Discover available storage on Proxmox host for secondary disk provisioning.

This script queries a Proxmox host to identify:
1. The OS disk (used by Proxmox itself via LVM/pve VG)
2. Available secondary disks (raw, unused disks)
3. Existing storage pools that could be used for VM disks

Output: JSON with disk information for Ansible consumption

Environment Variables:
  PROXMOX_HOST: Proxmox API host (required)
  PROXMOX_USER: API user (required for API mode)
  PROXMOX_PASSWORD: API password (required for API mode)
  PROXMOX_NODE: Target node name (default: pve)
  PROXMOX_VERIFY_SSL: Verify SSL certificates (default: false)
"""

import os
import sys
import json
import logging
from proxmoxer import ProxmoxAPI

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def getenv(key, default=None):
    val = os.environ.get(key)
    return val if val is not None else default


def getenv_bool(key, default=False):
    val = getenv(key, None)
    if val is None:
        return bool(default)
    return val.lower() in ("1", "true", "yes", "on")


def bytes_to_gib(size_bytes):
    """Convert bytes to GiB (base-2)."""
    return size_bytes / (1024 ** 3)


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


def get_storage_info(proxmox, node):
    """Get information about Proxmox storage pools."""
    try:
        storages = proxmox.nodes(node).storage.get()
        return {s['storage']: s for s in storages}
    except Exception as e:
        logging.warning(f"Failed to get storage info: {e}")
        return {}


def get_disk_info(proxmox, node):
    """
    Get disk information from Proxmox node.
    
    Uses the Proxmox API to list disks, which is more reliable than parsing lsblk.
    """
    try:
        # Proxmox 7+ has a disks API
        disks = proxmox.nodes(node).disks.list.get()
        return disks
    except Exception as e:
        logging.warning(f"Proxmox disks API not available: {e}")
        return None


def get_lvm_info(proxmox, node):
    """Get LVM thin pool information from Proxmox."""
    try:
        lvmthin = proxmox.nodes(node).disks.lvmthin.get()
        return lvmthin
    except Exception as e:
        logging.warning(f"Failed to get LVM thin info: {e}")
        return []


def discover_secondary_disks(proxmox, node):
    """
    Discover disks available for secondary storage provisioning.
    
    Returns a list of disks that are:
    - Not used by Proxmox OS (not in pve VG)
    - Not already partitioned with data
    - Suitable for VM storage
    """
    result = {
        "os_disk": None,
        "secondary_disks": [],
        "existing_storage_pools": [],
        "total_secondary_gib": 0,
    }

    # Get disk list from Proxmox API
    disks = get_disk_info(proxmox, node)
    if not disks:
        logging.error("Could not retrieve disk information from Proxmox")
        return result

    for disk in disks:
        devpath = disk.get('devpath', '')
        size_bytes = disk.get('size', 0)
        size_gib = round(bytes_to_gib(size_bytes), 1)
        used = disk.get('used', '')
        gpt = disk.get('gpt', 0)
        model = disk.get('model', 'unknown')
        serial = disk.get('serial', 'unknown')
        disk_type = disk.get('type', 'unknown')

        disk_info = {
            "devpath": devpath,
            "size_bytes": size_bytes,
            "size_gib": size_gib,
            "model": model,
            "serial": serial,
            "type": disk_type,
            "used": used,
            "gpt": gpt,
        }

        # Determine if this is the OS disk or available for secondary use
        # OS disk indicators: LVM, pve VG, mounted partitions, BIOS boot
        if used and ('LVM' in used or 'pve' in used.lower() or 'BIOS' in used or 'mounted' in used.lower()):
            # This disk is used by Proxmox OS/LVM
            result["os_disk"] = disk_info
            logging.info(f"OS disk identified: {devpath} ({size_gib} GiB) - {used}")
        elif not used or used == '':
            # Unused disk - available for secondary storage
            result["secondary_disks"].append(disk_info)
            result["total_secondary_gib"] += size_gib
            logging.info(f"Secondary disk available: {devpath} ({size_gib} GiB)")
        else:
            # Disk is used for something else (e.g., existing storage pool)
            logging.info(f"Disk in use: {devpath} ({size_gib} GiB) - {used}")

    # Get existing storage pools
    storages = get_storage_info(proxmox, node)
    for name, storage in storages.items():
        if storage.get('type') in ('lvmthin', 'lvm', 'dir', 'zfspool'):
            pool_info = {
                "name": name,
                "type": storage.get('type'),
                "content": storage.get('content', ''),
                "total": storage.get('total', 0),
                "avail": storage.get('avail', 0),
                "used": storage.get('used', 0),
            }
            result["existing_storage_pools"].append(pool_info)

    return result


def main():
    node = getenv("PROXMOX_NODE", "pve")
    
    logging.info(f"Discovering storage on Proxmox node: {node}")
    
    proxmox = get_proxmox_connection()
    
    result = discover_secondary_disks(proxmox, node)
    
    # Output as JSON for Ansible consumption
    print(json.dumps(result, indent=2))
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
