#!/usr/bin/env python3

import os
import sys
import logging
from proxmoxer import ProxmoxAPI

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def getenv(key, default=None):
    val = os.environ.get(key)
    return val if val is not None else default

def getenv_int(key, default):
    val = getenv(key, None)
    # If an environment value exists, return its integer conversion
    if val is not None:
        try:
            return int(val)
        except Exception:
            # fallback to default handling below
            pass
    # If default is None, propagate None (used for optional values like VMID)
    if default is None:
        return None
    # Otherwise, try to convert default to int
    try:
        return int(default)
    except Exception:
        return None

def getenv_bool(key, default=False):
    val = getenv(key, None)
    if val is None:
        return bool(default)
    return val.lower() in ("1", "true", "yes", "on")

def main():
    # Connection settings
    host = getenv("PROXMOX_HOST")
    if not host:
        logging.error("PROXMOX_HOST must be set in the environment")
        sys.exit(1)

    # Support token auth or password auth
    token_name = getenv("PROXMOX_TOKEN_NAME")
    token_value = getenv("PROXMOX_TOKEN_VALUE")
    # Accept PROXMOX_PASSWORD or PROXMOX_PASS
    user = getenv("PROXMOX_USER")
    password = getenv("PROXMOX_PASSWORD", getenv("PROXMOX_PASS"))
    # Verify SSL: default True (verify), can be set to false via env
    verify_ssl = getenv_bool("PROXMOX_VERIFY_SSL", True)

    try:
        if token_name and token_value:
            proxmox = ProxmoxAPI(host, token_name=token_name, token_value=token_value, verify_ssl=verify_ssl)
        else:
            if not user or not password:
                logging.error("PROXMOX_USER and PROXMOX_PASSWORD (or PROXMOX_PASS) must be set if not using token auth")
                sys.exit(1)
            proxmox = ProxmoxAPI(host, user=user, password=password, verify_ssl=verify_ssl)
    except Exception as e:
        logging.error(f"Failed to connect to Proxmox API: {e}")
        sys.exit(1)

    # Node and basic VM config with defaults mirroring the Proxmox UI wizard
    node = getenv("PROXMOX_NODE", "pve")
    name = getenv("VM_NAME", getenv("NAME", "proxmox-vm"))
    vmid = getenv_int("VMID", None)

    # If a VM with the same name already exists on the node, print and exit (do not create a duplicate)
    try:
        existing_vms = proxmox.nodes(node).qemu.get()
        for ev in existing_vms:
            if ev.get('name') == name:
                existing_vmid = ev.get('vmid') or ev.get('vmid')
                print(f"Existing VM {existing_vmid}...")
                sys.exit(0)
    except Exception:
        # ignore errors querying existing VMs and continue with creation flow
        pass

    # If no VMID provided, ask proxmox for nextid
    if vmid is None:
        try:
            vmid = proxmox.cluster.nextid.get()
        except Exception as e:
            logging.error(f"Failed to get next VMID: {e}")
            sys.exit(1)

    # Accept either VM_CORES/VM_MEMORY or CORES/MEMORY env names
    cores = getenv_int("VM_CORES", getenv_int("CORES", 2))
    sockets = getenv_int("SOCKETS", 1)
    memory = getenv_int("VM_MEMORY", getenv_int("MEMORY", 2048))   # MiB
    balloon = getenv_int("BALLOON", 0)

    # Disk defaults - accept several env names
    storage = getenv("PROXMOX_STORAGE", getenv("STORAGE", "local-lvm"))
    disk_size = getenv_int("VM_DISK_SIZE", getenv_int("DISK_SIZE", 8))  # GiB
    scsihw = getenv("SCSI_HW", "virtio-scsi-pci")

    # Ensure selected storage supports VM images. If not, try to find a suitable fallback on the node.
    try:
        storages = proxmox.nodes(node).storage.get()
        # Normalize content and map storage name -> content string
        storage_map = {s.get('storage'): str(s.get('content', '')).lower() for s in storages}
        sel_content = storage_map.get(storage)
        if not sel_content or ('images' not in sel_content and 'rootdir' not in sel_content):
            # Prefer local-lvm if available
            fallback = None
            if 'local-lvm' in storage_map and ('images' in storage_map['local-lvm'] or 'rootdir' in storage_map['local-lvm']):
                fallback = 'local-lvm'
            else:
                for name, content in storage_map.items():
                    if 'images' in content or 'rootdir' in content:
                        fallback = name
                        break
            if fallback:
                logging.warning(f"Selected storage '{storage}' does not support VM images; using '{fallback}' instead")
                storage = fallback
            else:
                logging.error(f"No suitable storage found on node {node} that supports VM images. Available: {list(storage_map.keys())}")
                sys.exit(1)
    except Exception:
        # If we cannot query storages, continue and let the API validate
        pass

    # Network defaults - build net0 from model and bridge if provided
    net_model = getenv("VM_NET_MODEL", getenv("NET_MODEL", "virtio"))
    net_bridge = getenv("VM_NET_BRIDGE", getenv("NET_BRIDGE", "vmbr0"))
    net0 = getenv("NET0", f"{net_model},bridge={net_bridge}")

    # OS/type and boot
    ostype = getenv("OSTYPE", "l26")      # Linux
    # Accept VM_ISO_IMAGE in multiple forms:
    # - full volume id like 'local:iso/ubuntu.iso' -> used as-is
    # - bare filename like 'ubuntu.iso' -> prefix with ISO_STORAGE (default 'local') and 'iso/'
    iso_raw = getenv("VM_ISO_IMAGE", getenv("ISO"))
    iso = None
    if iso_raw:
        if ":" in iso_raw:
            iso = iso_raw
        else:
            iso_storage = getenv("ISO_STORAGE", "local")
            iso = f"{iso_storage}:iso/{iso_raw}"

    # Cloud-init defaults (used when using cloud-init disk)
    # Set DISABLE_CLOUDINIT to true to disable cloud-init entirely (default: true -> disabled)
    disable_cloudinit = getenv_bool("DISABLE_CLOUDINIT", True)
    # CLOUDINIT can still be used to explicitly enable cloud-init if DISABLE_CLOUDINIT is false
    use_cloudinit = False if disable_cloudinit else getenv_bool("CLOUDINIT", True)
    ciuser = getenv("CIUSER", "root")
    cipassword = getenv("CIPASSWORD")
    ipconfig0 = getenv("IPCONFIG0", getenv("VM_IP_ADDRESS", None))  # e.g. "ip=dhcp" or direct IP string

    # Default to starting the VM so the playbook's changed_when matches
    start_vm = getenv_bool("START_VM", True)
    # Auto-start VM at boot: enabled by default, can be disabled via ONBOOT=0/false
    onboot = getenv_bool("ONBOOT", True)
    # QEMU guest agent: enabled by default, can be disabled via QEMU_GUEST_AGENT=0/false
    qga = getenv_bool("QEMU_GUEST_AGENT", True)
    pool = getenv("POOL")
    tags = getenv("TAGS")

    # GPU passthrough configuration check (before building params)
    # If GPU passthrough is enabled, we must use Q35 machine type
    gpu_pci = getenv("GPU_PCI_ADDRESS")
    machine_type = "q35" if gpu_pci else None  # Q35 required for PCIe passthrough

    # Build parameters for API call
    params = {
        "vmid": int(vmid),
        "name": name,
        "cores": int(cores),
        "sockets": int(sockets),
        "memory": int(memory),
        "balloon": int(balloon),
        "ostype": ostype,
        "scsihw": scsihw,
        "net0": net0,
        # enable or disable the QEMU guest agent
        "agent": 1 if qga else 0,
        # auto-start VM at boot
        "onboot": 1 if onboot else 0,
    }

    # Set machine type if needed (Q35 required for PCIe passthrough)
    if machine_type:
        params["machine"] = machine_type

    # Disk parameter: mirror UI by using scsi0 on local-lvm with size in GiB
    params["scsi0"] = f"{storage}:{disk_size}"

    # If an ISO is provided, attach it as cdrom and set boot order to prefer the installed disk
    if iso:
        params["ide2"] = f"{iso},media=cdrom"
        # If the user did not explicitly set BOOT, prefer the installed disk (scsi0) then CD (ide2)
        if getenv("BOOT") is None:
            params["boot"] = "order=scsi0;ide2"
            params["bootdisk"] = "scsi0"
        else:
            params["boot"] = getenv("BOOT")
    else:
        # If no ISO and cloudinit enabled, add a cloud-init disk
        if use_cloudinit:
            params["ide2"] = f"{storage}:cloudinit"
            # cloud-init params
            params["ciuser"] = ciuser
            if cipassword:
                params["cipassword"] = cipassword
            if ipconfig0:
                # If ipconfig0 looks like a direct IP address, format as ip=.../24
                if ipconfig0 and ("ip=" in ipconfig0 or "dhcp" in ipconfig0):
                    params["ipconfig0"] = ipconfig0
                else:
                    # attempt to put into ipconfig0 form (user may supply raw IP)
                    params["ipconfig0"] = f"ip={ipconfig0}"
        # Ensure VM will boot from the created disk by default if BOOT not specified
        if getenv("BOOT") is None:
            params["boot"] = "order=scsi0"
            params["bootdisk"] = "scsi0"

    if pool:
        params["pool"] = pool
    if tags:
        params["tags"] = tags

    # GPU passthrough configuration (optional)
    # GPU_PCI_ADDRESS already checked above when setting machine_type
    if gpu_pci:
        # hostpci0: PCI_ADDRESS,pcie=1,x-vga=0
        # - pcie=1: Present as PCIe device (required for modern GPUs)
        # - x-vga=0: Not primary VGA (headless mode for Kubernetes nodes)
        # - Omitting function includes all functions (e.g., 01:00.0 GPU + 01:00.1 Audio)
        # - Q35 machine type is set above when gpu_pci is detected
        params["hostpci0"] = f"{gpu_pci},pcie=1,x-vga=0"
        logging.info(f"GPU passthrough enabled: {gpu_pci} (machine: q35)")

    logging.info(f"Creating VM {name} (vmid={vmid}) on node {node} with params: cores={cores}, memory={memory} MiB, disk={disk_size}GiB, net={net0}")

    try:
        res = proxmox.nodes(node).qemu.post(**params)
        logging.info(f"Create request accepted: {res}")
    except Exception as e:
        logging.error(f"Failed to create VM: {e}")
        sys.exit(1)

    # Optionally start VM
    if start_vm:
        try:
            proxmox.nodes(node).qemu(vmid).status.start.post()
            logging.info(f"VM {vmid} started")
            # Print the exact string the playbook expects for changed_when and parsing
            print(f"Starting VM {vmid}")
        except Exception as e:
            logging.error(f"Failed to start VM {vmid}: {e}")
            # Still print vmid so downstream tasks can see something
            print(vmid)
            sys.exit(1)
    else:
        print(vmid)

if __name__ == "__main__":
    main()