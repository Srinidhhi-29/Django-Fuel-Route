Django Fuel Route (OSRM-only ready)
Quick start (Windows):
1. python -m venv venv
2. venv\Scripts\activate
3. pip install -r requirements.txt
4. python manage.py migrate
5. python manage.py runserver
6. Open: http://127.0.0.1:8000/demo/
Notes:
 - The app expects a CSV named 'fuel-prices-for-be-assessment.csv' in the project root or /mnt/data.
 - Uses router.project-osrm.org (no API key) for routing. You can set OSRM_BASE to a different OSRM endpoint.
