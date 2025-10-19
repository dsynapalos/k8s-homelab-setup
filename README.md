Repo to create ansible onboarding scripts for kubeadm onboarding clusters.

This is not production-ready, just a resource for studying kubernetes, virtualization, linux and generic homelab goodness.

First steps:
- run `sudo chmod +x init.sh && ./init.sh`
- create an .env file in the project folder containing the below information (also see [example.env](example.env)):
    ```
    K8S_CONTROL_1_IP={{ ssh IP of K8s control plane }}
    K8S_NODE_1_IP={{ ssh IP of K8s node }}
    K8S_SSH_USER={{ ssh user of K8s control/node }}
    K8S_SSH_KEY={{ path to ssh key of K8s control/node }}
    K8S_SSH_PUB_KEY={{ path to ssh key of K8s control/node }}
    K8S_VERSION={{ version of k8s to be installed }}
    CRIO_VERSION={{ version of cri-o to be installed}}
    CILIUM_VERSION={{ version of cillium to be installed }}
    CILIUM_LOADBALANCER_IPPOOL={{ range of reserved IPs to be assigned to LB services }}
    ANSIBLE_HOST_KEY_CHECKING={{ ignore vm key signature change }}
    ANSIBLE_VERBOSITY={{ ansible log verbosity }}
    UBUNTU_RELEASE_VERSION={{ version of ubuntu iso to download}}
    PROXMOX_API_USER={{ proxmox user }}
    PROXMOX_API_PASSWORD={{ proxmox password }}
    PROXMOX_API_HOST={{ proxmox host ip }}
    PROXMOX_LOCAL_STORAGE={{ proxmox storage for iso }}
    PROXMOX_NODE={{ proxmox node name }}
    VM_GATEWAY={{ gateway ip }}
    VM_NAMESERVER= {{ dns ip }}
    VM_NET_BRIDGE={{ proxmox net bridge }}
    VM_NET_MODEL={{ proxmox net model}}
    K8S_CONTROL_1_MEM_MB={{ memory assignment for control }}
    K8S_CONTROL_1_DISK_GB={{ disk assignment for control }}
    K8S_CONTROL_1_CPU={{ cpu count for control }}
    K8S_NODE_1_MEM_MB={{ memory assignment for node }}
    K8S_NODE_1_DISK_GB={{ disk assignment for node }}
    K8S_NODE_1_CPU={{ cpu count for node }}
    ```
- run `python3 setup-clusters.py`
- use kubectl, helm, cilium and hubble cli to connect to the cluster
- ???
- profit

What this repo expects:
- A functional proxmox cluster, or standalone ubuntu os instances.
- A control environment. I use ubuntu WSL.
- The user of the control environment will be used to leverage any CLI interface.

What this repo will do:
- Download and modify the ubuntu server ISO for autoinstall.
- Create the proxmox VMs specified in the inventory.
- Assign static IPs and user/hostname.
- Install kubernetes-adjacent packages (crio,kubectl,kubeadm,etc)
- Install kubernetes with Cilium proxy
- Copy the generated kubeconfig to /home/{local_user}/.kube/config.
- Create Cilium l7 dns policy, assign a subnetspace to LB instances and advertise their adresses in l4.
- Install ArgoCD manifests.

Runtime duration:
Will download ~3GB ubuntu image in first run modify it and upload to proxmox.
If ubuntu image is already present in roles/provision_infra/files/iso and the autoinstall version is upladed to proxmox (after a single execution) end-to-end run with VM creation takes ~17 min.