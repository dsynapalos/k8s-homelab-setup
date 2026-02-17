import ansible_runner
import os
import shutil
from dotenv import load_dotenv

load_dotenv()

dir_path = os.path.dirname(os.path.realpath(__file__))
artifact_path = os.path.join(dir_path, 'artifacts')
venv_bin = os.path.join(dir_path, '.venv', 'bin')

try:
    shutil.rmtree(artifact_path)
except Exception as e:
    pass

env = os.environ.copy()
env['PATH'] = f"{venv_bin}:{env.get('PATH', '')}"
env['ANSIBLE_STDOUT_CALLBACK'] = 'yaml'

r = ansible_runner.run(
        private_data_dir=dir_path,
        playbook=os.path.join(dir_path, 'expose_ca.yaml'),
        envvars=env
    )

print(r.stats)
