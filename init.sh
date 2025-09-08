sudo apt install -y python3 python3-pip python3-venv
python3 -m venv venv
source venv/bin/activate
pip3 install ansible-runner ansible python-dotenv kubernetes