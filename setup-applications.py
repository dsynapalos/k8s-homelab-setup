import ansible_runner
import os 
import shutil
from dotenv import load_dotenv
import time

load_dotenv()

dir_path = os.path.dirname(os.path.realpath(__file__))
artifact_path = os.path.join(dir_path, 'artifacts')
try:
    shutil.rmtree(artifact_path)
except Exception as e:
    pass

start_time = time.time()

r = ansible_runner.run(
        private_data_dir=dir_path, 
        playbook=os.path.join(dir_path,'setup_applications.yaml')
    )

print(r.stats)
minutes, seconds = divmod(int(time.time() - start_time), 60)
print(f"--- {minutes}:{seconds:02d} ---")