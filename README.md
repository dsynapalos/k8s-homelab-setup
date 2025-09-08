Repo to create ansible onboarding scripts for kubeadm onboarding clusters.

First steps:
- run `sudo chmod +x init.sh && ./init.sh`
- create an .env file in the project folder containing the below information:
    ```
    K8S_CONTROL_IP={{ ssh IP of K8s control plane }}
    K8S_SSH_USER={{ ssh user of K8s control plane }}
    K8S_SSH_KEY={{ path to ssh key of K8s control plane }}
    K8S_VERSION={{ version of k8s to be installed }}
    CRIO_VERSION={{ version of cri-o to be installed}}
    CILIUM_VERSION={{ version of cillium to be installed }}
    ```
- run `python3 setup-clusters.py`
- ???
- profit