#!/bin/bash
cd /var/www/Dynamometer-FHIR || exit 1

# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Restart FastAPI systemd service
sudo systemctl restart fastapi

# Optional: check if the service is running
systemctl is-active --quiet fastapi || exit 1
