import ansible_runner
import os 
import shutil
from dotenv import load_dotenv
import time

load_dotenv()

dir_path = os.path.dirname(os.path.realpath(__file__))
artifact_path = os.path.join(dir_path, 'artifacts')
venv_bin = os.path.join(dir_path, '.venv', 'bin')

# Clean ansible_runner state to prevent stale cached env vars from overriding .env
for cleanup_path in [artifact_path, os.path.join(dir_path, 'env', 'envvars')]:
    try:
        if os.path.isdir(cleanup_path):
            shutil.rmtree(cleanup_path)
        elif os.path.isfile(cleanup_path):
            os.remove(cleanup_path)
    except Exception:
        pass

start_time = time.time()

# Set PATH to include venv bin directory for ansible-playbook
env = os.environ.copy()
env['PATH'] = f"{venv_bin}:{env.get('PATH', '')}"
env['ANSIBLE_STDOUT_CALLBACK'] = 'yaml'

r = ansible_runner.run(
        private_data_dir=dir_path, 
        playbook=os.path.join(dir_path,'setup_cluster.yaml'),
        envvars=env
    )

print(r.stats)
minutes, seconds = divmod(int(time.time() - start_time), 60)
print(f"--- {minutes}:{seconds:02d} ---")