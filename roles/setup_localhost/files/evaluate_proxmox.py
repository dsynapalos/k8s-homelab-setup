import os
import sys
from proxmoxer import ProxmoxAPI

# Get environment variables
PROXMOX_HOST = os.environ.get("PROXMOX_HOST")
PROXMOX_USER = os.environ.get("PROXMOX_USER")
PROXMOX_PASSWORD = os.environ.get("PROXMOX_PASSWORD")
PROXMOX_VERIFY_SSL = os.environ.get("PROXMOX_VERIFY_SSL", "false").lower() == "true"
PROXMOX_NODE = os.environ.get("PROXMOX_NODE")
PROXMOX_STORAGE = os.environ.get("PROXMOX_STORAGE")
IMAGE = os.environ.get("IMAGE")


if not all([PROXMOX_HOST, PROXMOX_USER, PROXMOX_PASSWORD, PROXMOX_NODE, PROXMOX_STORAGE, IMAGE]):
    print("Missing one or more required environment variables.")
    sys.exit(1)

# Connect to Proxmox API
proxmox = ProxmoxAPI(
    PROXMOX_HOST,
    user=PROXMOX_USER,
    password=PROXMOX_PASSWORD,
    verify_ssl=PROXMOX_VERIFY_SSL
)

# List content in the specified storage
try:
    storage_content = proxmox.nodes(PROXMOX_NODE).storage(PROXMOX_STORAGE).content.get()
except Exception as e:
    print(f"Error accessing storage: {e}")
    sys.exit(1)

# Check if the Ubuntu image exists
image_found = False
for item in storage_content:
    if 'volid' in item and IMAGE in item['volid']:
        image_found = True
        print(f"Image '{IMAGE}' found in storage '{PROXMOX_STORAGE}' on node '{PROXMOX_NODE}'.")
        break

if not image_found:
    print(f"Image '{IMAGE}' NOT found in storage '{PROXMOX_STORAGE}' on node '{PROXMOX_NODE}'.")
    sys.exit(2)