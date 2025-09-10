#!/bin/bash
cd /var/www/Dynamometer-FHIR
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart fastapi
