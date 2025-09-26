#!/usr/bin/env python3
import os
import sys
import time

try:
    from proxmoxer import ProxmoxAPI
except Exception as e:
    print('Missing dependency proxmoxer:', e, file=sys.stderr)
    sys.exit(10)


def str2bool(v):
    return str(v).lower() in ('1', 'true', 'yes')


def connect_proxmox():
    host = os.environ.get('PROXMOX_HOST')
    user = os.environ.get('PROXMOX_USER')
    password = os.environ.get('PROXMOX_PASSWORD')
    verify_ssl = str2bool(os.environ.get('PROXMOX_VERIFY_SSL', 'false'))

    if not all([host, user, password]):
        print('Missing PROXMOX_HOST/PROXMOX_USER/PROXMOX_PASSWORD environment variables', file=sys.stderr)
        sys.exit(3)

    try:
        return ProxmoxAPI(host, user=user, password=password, verify_ssl=verify_ssl)
    except Exception as e:
        print('Failed to connect to Proxmox API:', e, file=sys.stderr)
        sys.exit(4)


def _get_agent_network(node, proxmox, vmid):
    # Try GET then POST; handle different response formats
    try:
        resp = proxmox.nodes(node).qemu(vmid).agent('network-get-interfaces').get()
    except Exception:
        try:
            resp = proxmox.nodes(node).qemu(vmid).agent('network-get-interfaces').post()
        except Exception:
            return None
    # Normalize responses that wrap the result
    if isinstance(resp, dict) and 'result' in resp:
        return resp['result']
    return resp


def _parse_interfaces_for_ipv4(ifaces):
    if not ifaces:
        return None
    # Interfaces might be a dict with 'interfaces' key or a list
    candidates = []
    if isinstance(ifaces, dict) and 'interfaces' in ifaces:
        candidates = ifaces['interfaces'] or []
    elif isinstance(ifaces, list):
        candidates = ifaces
    elif isinstance(ifaces, dict):
        # single interface
        candidates = [ifaces]

    for iface in candidates:
        # ip address lists may be under different keys
        ip_entries = iface.get('ip-addresses') or iface.get('ip_addresses') or iface.get('ip_addresses_v4') or []
        for e in ip_entries:
            # keys vary: try several
            ip = e.get('ip-address') or e.get('ip_address') or e.get('address') or e.get('ip')
            iptype = e.get('ip-address-type') or e.get('ip_address_type') or e.get('type') or ''
            if not ip:
                continue
            # prefer IPv4, skip link-local
            ip_str = str(ip)
            if str(iptype).lower().startswith('ipv4') and not (ip_str.startswith('169.254') or ip_str.startswith('127.0')):
                return ip_str.strip()
            if '.' in ip_str and not (ip_str.startswith('169.254') or ip_str.startswith('127.0')):
                return ip_str.strip()
    return None


def fetch_guest_ip(proxmox, node, vmid, timeout=600, interval=60):
    deadline = time.time() + timeout
    poll = 0
    while time.time() < deadline:
        poll += 1
        try:
            ifaces = _get_agent_network(node, proxmox, vmid)
            # Debug: print network interfaces returned by the guest agent to stderr
            try:
                import json
                print(f"DEBUG: vm {vmid} agent interfaces (poll {poll}): {json.dumps(ifaces, default=str)}", file=sys.stderr)
            except Exception:
                print(f"DEBUG: vm {vmid} agent interfaces (poll {poll}): {repr(ifaces)}", file=sys.stderr)

            ip = _parse_interfaces_for_ipv4(ifaces)
            if ip:
                return ip
        except Exception:
            # ignore transient errors (agent not ready yet)
            pass
        time.sleep(interval)
    return None


def main():
    # Read configuration from environment variables
    vmid_env = os.environ.get('VM_ID') or os.environ.get('VMID')
    node = os.environ.get('PROXMOX_NODE')
    try:
        timeout = int(os.environ.get('POLL_TIMEOUT', '600'))
    except ValueError:
        timeout = 600
    try:
        interval = int(os.environ.get('POLL_INTERVAL', '60'))
    except ValueError:
        interval = 60

    if not vmid_env:
        print('VM ID not specified via VM_ID or VMID environment variable', file=sys.stderr)
        sys.exit(3)
    try:
        vmid = int(vmid_env)
    except ValueError:
        print('Invalid VM ID specified in VM_ID/VMID', file=sys.stderr)
        sys.exit(3)

    if not node:
        print('Proxmox node not specified via PROXMOX_NODE env var', file=sys.stderr)
        sys.exit(3)

    proxmox = connect_proxmox()
    ip = fetch_guest_ip(proxmox, node, vmid, timeout=timeout, interval=interval)
    if ip:
        print(ip)
        sys.exit(0)
    else:
        # no IP found within timeout
        print('', end='')
        sys.exit(2)


if __name__ == '__main__':
    main()
