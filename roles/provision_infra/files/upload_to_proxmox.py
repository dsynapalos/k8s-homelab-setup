#!/usr/bin/env python3
import os
import sys

try:
    from proxmoxer import ProxmoxAPI
except Exception as e:
    print('Missing dependency proxmoxer:', e)
    sys.exit(10)


def str2bool(v):
    return str(v).lower() in ('1', 'true', 'yes')

PROXMOX_HOST = os.environ.get('PROXMOX_HOST')
PROXMOX_USER = os.environ.get('PROXMOX_USER')
PROXMOX_PASSWORD = os.environ.get('PROXMOX_PASSWORD')
PROXMOX_VERIFY_SSL = os.environ.get('PROXMOX_VERIFY_SSL', 'false')
PROXMOX_NODE = os.environ.get('PROXMOX_NODE')
PROXMOX_STORAGE = os.environ.get('PROXMOX_STORAGE')
IMAGE = os.environ.get('IMAGE')

if not (PROXMOX_HOST and PROXMOX_USER and PROXMOX_PASSWORD and PROXMOX_NODE and PROXMOX_STORAGE and IMAGE):
    print('Missing required environment variables')
    sys.exit(3)

verify_ssl = str2bool(PROXMOX_VERIFY_SSL)

try:
    proxmox = ProxmoxAPI(PROXMOX_HOST, user=PROXMOX_USER, password=PROXMOX_PASSWORD, verify_ssl=verify_ssl)
except Exception as e:
    print('Failed to connect to Proxmox API:', e)
    sys.exit(4)

candidates = []
# Also try relative to this script's directory (role files dir)
script_dir = os.path.dirname(os.path.realpath(__file__))
candidates.append(os.path.join(script_dir, 'iso', IMAGE))

# Also try absolute path in case full path was provided
candidates.append(IMAGE)

iso_path = None
for p in candidates:
    if p and os.path.exists(p):
        iso_path = p
        break

if not iso_path:
    print('ISO not found. Tried paths:')
    for p in candidates:
        print(' -', p)
    sys.exit(5)

try:
    with open(iso_path, 'rb') as f:
        print(f'Uploading {iso_path} to storage {PROXMOX_STORAGE} on node {PROXMOX_NODE}...')
        resp = proxmox.nodes(PROXMOX_NODE).storage(PROXMOX_STORAGE).upload.post(filename=f, content='iso')
    print('Upload completed, response:', resp)
    sys.exit(0)
except Exception as e:
    print('Upload failed:', e)
    sys.exit(2)
