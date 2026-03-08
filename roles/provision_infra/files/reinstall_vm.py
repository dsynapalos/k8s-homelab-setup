#!/usr/bin/env python3
"""Trigger an autoinstall retry for a VM that failed to obtain a DHCP address.

Steps performed:
  1. Set boot order to ISO first (ide2;scsi0) AND disable guest reboot
     (reboot=0) so the VM halts instead of rebooting after autoinstall.
  2. Stop the VM (wait until fully stopped).
  3. Start the VM — begins unattended autoinstall from the ISO.
  4. Wait for the VM to stop again (autoinstall finishes → guest reboots
     → VM halts because reboot=0).
  5. Revert boot order to disk first (scsi0;ide2) AND re-enable guest
     reboot (reboot=1).  Because the VM is stopped, config changes apply
     immediately — no "pending" state issues.
  6. Start the VM — boots from disk into the freshly installed OS.

Why reboot=0?
  Proxmox config changes made to a running VM go into a "pending" state
  and are only applied on a Proxmox-managed reboot (qm reboot), NOT on
  a guest-initiated reboot.  The autoinstall ISO triggers a guest reboot
  when installation completes, so any pending boot-order change would be
  ignored and the VM would boot from the ISO a second time.  Setting
  reboot=0 causes the VM to stop on guest reboot, giving us a window to
  change the boot order while the VM is stopped (where changes apply
  immediately) before starting it again.

Environment variables (same naming convention as create_vm.py / poll_for_ip.py):
  PROXMOX_HOST, PROXMOX_USER, PROXMOX_PASSWORD, PROXMOX_VERIFY_SSL
  PROXMOX_NODE  — Proxmox node hosting the VM
  VM_ID         — numeric VMID of the target VM
"""

import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

try:
    from proxmoxer import ProxmoxAPI
except ImportError as exc:
    logging.error("Missing dependency proxmoxer: %s", exc)
    sys.exit(10)

# Maximum time (seconds) to wait for the VM to stop after autoinstall.
# Ubuntu autoinstall typically takes 5-15 min; 25 min gives ample margin.
AUTOINSTALL_TIMEOUT = 1500


def str2bool(v):
    return str(v).lower() in ("1", "true", "yes", "on")


def wait_for_vm_stop(proxmox, node, vmid, timeout, label=""):
    """Poll until the VM reaches 'stopped' state or *timeout* seconds elapse."""
    prefix = f"[{label}] " if label else ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            status = proxmox.nodes(node).qemu(vmid).status.current.get()
            if status.get("status") == "stopped":
                logging.info("%sVM %s stopped.", prefix, vmid)
                return True
        except Exception:
            pass
    logging.warning("%sVM %s did not reach 'stopped' within %ss.", prefix, vmid, timeout)
    return False


def main():
    host = os.environ.get("PROXMOX_HOST")
    user = os.environ.get("PROXMOX_USER")
    password = os.environ.get("PROXMOX_PASSWORD")
    verify_ssl = str2bool(os.environ.get("PROXMOX_VERIFY_SSL", "false"))
    node = os.environ.get("PROXMOX_NODE")
    vmid_raw = os.environ.get("VM_ID") or os.environ.get("VMID")

    if not all([host, user, password, node, vmid_raw]):
        logging.error("Required env vars: PROXMOX_HOST, PROXMOX_USER, PROXMOX_PASSWORD, PROXMOX_NODE, VM_ID")
        sys.exit(1)

    try:
        vmid = int(vmid_raw)
    except ValueError:
        logging.error("VM_ID must be an integer, got: %s", vmid_raw)
        sys.exit(1)

    try:
        proxmox = ProxmoxAPI(host, user=user, password=password, verify_ssl=verify_ssl)
    except Exception as exc:
        logging.error("Failed to connect to Proxmox API: %s", exc)
        sys.exit(1)

    # --- Step 1: Set boot order to ISO first + disable guest reboot ---------
    logging.info("Setting boot=ide2;scsi0 and reboot=0 for VM %s", vmid)
    try:
        proxmox.nodes(node).qemu(vmid).config.put(
            boot="order=ide2;scsi0",
            bootdisk="ide2",
            reboot=0,
        )
    except Exception as exc:
        logging.error("Failed to update VM config: %s", exc)
        sys.exit(1)

    # --- Step 2: Stop the VM ------------------------------------------------
    logging.info("Stopping VM %s ...", vmid)
    try:
        proxmox.nodes(node).qemu(vmid).status.stop.post()
    except Exception as exc:
        logging.error("Failed to stop VM: %s", exc)
        sys.exit(1)

    if not wait_for_vm_stop(proxmox, node, vmid, timeout=120, label="pre-install"):
        logging.error("VM %s did not stop in time — aborting.", vmid)
        sys.exit(1)

    # --- Step 3: Start the VM (boots from autoinstall ISO) ------------------
    logging.info("Starting VM %s (booting from autoinstall ISO) ...", vmid)
    try:
        proxmox.nodes(node).qemu(vmid).status.start.post()
    except Exception as exc:
        logging.error("Failed to start VM: %s", exc)
        sys.exit(1)

    # --- Step 4: Wait for VM to stop (autoinstall done → guest reboots → VM halts)
    logging.info(
        "Waiting up to %ss for autoinstall to complete "
        "(VM will halt on guest reboot because reboot=0) ...",
        AUTOINSTALL_TIMEOUT,
    )
    if not wait_for_vm_stop(proxmox, node, vmid, timeout=AUTOINSTALL_TIMEOUT, label="autoinstall"):
        # Autoinstall did not finish in time — restore reboot=1 and bail out.
        logging.error("Autoinstall did not complete within %ss.", AUTOINSTALL_TIMEOUT)
        try:
            proxmox.nodes(node).qemu(vmid).config.put(reboot=1)
        except Exception:
            pass
        sys.exit(1)

    # --- Step 5: Revert boot order + re-enable guest reboot -----------------
    # VM is stopped, so config changes apply immediately (no pending state).
    logging.info("Reverting boot=scsi0;ide2 and reboot=1 for VM %s", vmid)
    try:
        proxmox.nodes(node).qemu(vmid).config.put(
            boot="order=scsi0;ide2",
            bootdisk="scsi0",
            reboot=1,
        )
    except Exception as exc:
        logging.error("Failed to revert VM config: %s", exc)
        sys.exit(1)

    # --- Step 6: Start the VM (boots from disk) -----------------------------
    logging.info("Starting VM %s (booting from disk) ...", vmid)
    try:
        proxmox.nodes(node).qemu(vmid).status.start.post()
    except Exception as exc:
        logging.error("Failed to start VM: %s", exc)
        sys.exit(1)

    logging.info("Autoinstall retry complete for VM %s — booting from disk.", vmid)
    print(f"Reinstall triggered for VM {vmid}")
    sys.exit(0)


if __name__ == "__main__":
    main()
