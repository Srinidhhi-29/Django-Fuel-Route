import os, json, math
from django.views import View
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.shortcuts import render
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, 'fuel-prices-for-be-assessment.csv')
if not os.path.exists(CSV_PATH):
    CSV_PATH = '/mnt/data/fuel-prices-for-be-assessment.csv'

def haversine(a,b):
    R=3958.8
    lat1,lon1=math.radians(a[0]),math.radians(a[1])
    lat2,lon2=math.radians(b[0]),math.radians(b[1])
    dlat=lat2-lat1; dlon=lon2-lon1
    x=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(x))

def parse_coord(s):
    if isinstance(s, str) and ',' in s:
        parts = s.split(',')
        try:
            lat=float(parts[0].strip()); lon=float(parts[1].strip())
            return (lat,lon)
        except:
            return None
    return None

def geocode_address(address):
    url = 'https://nominatim.openstreetmap.org/search'
    params = {'q': address,'format':'json','limit':1}
    r = requests.get(url, params=params, headers={'User-Agent':'django-fuel-route/1.0'}, timeout=10)
    if r.status_code==200 and r.json():
        item=r.json()[0]
        return (float(item['lat']), float(item['lon']))
    return None

def reverse_geocode(lat,lon):
    url = 'https://nominatim.openstreetmap.org/reverse'
    params = {'lat':lat,'lon':lon,'format':'json'}
    r = requests.get(url, params=params, headers={'User-Agent':'django-fuel-route/1.0'}, timeout=10)
    if r.status_code==200:
        j = r.json()
        addr = j.get('address',{})
        state = addr.get('state')
        city = addr.get('city') or addr.get('town') or addr.get('village')
        return city, state
    return None, None

def load_prices():
    import pandas as pd
    if os.path.exists(CSV_PATH):
        df = pd.read_csv(CSV_PATH, dtype=str)
    else:
        raise FileNotFoundError("CSV file not found at: %s" % CSV_PATH)
    if 'Retail Price' in df.columns:
        df['Retail Price'] = df['Retail Price'].str.replace('[^0-9.]','', regex=True).astype(float)
    if 'lat' in df.columns: df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
    if 'lon' in df.columns: df['lon'] = pd.to_numeric(df['lon'], errors='coerce')
    return df

@method_decorator(csrf_exempt, name='dispatch')
class RouteAPIView(View):
    def post(self, request):
        print("RAW BODY:", request.body)
        try:
            body = json.loads(request.body.decode())
            print("PARSED JSON:", body)
        except Exception as e:
            return HttpResponseBadRequest(f'invalid json: {str(e)}')

        start = body.get('start'); finish = body.get('finish')
        if not start or not finish:
            return HttpResponseBadRequest('start and finish required')

        start_coord = parse_coord(start) or geocode_address(start)
        finish_coord = parse_coord(finish) or geocode_address(finish)
        if not start_coord or not finish_coord:
            return HttpResponseBadRequest('could not geocode start/finish')

        # Use public OSRM server that supports global routing
        osrm_base = os.environ.get('OSRM_BASE','https://router.project-osrm.org')
        coords = f"{start_coord[1]},{start_coord[0]};{finish_coord[1]},{finish_coord[0]}"
        url = f"{osrm_base}/route/v1/driving/{coords}?overview=full&geometries=geojson&steps=false"
        print('OSRM URL:', url)
        try:
            r = requests.get(url, timeout=30)
            print('OSRM STATUS:', r.status_code)
            if r.status_code != 200:
                return HttpResponseBadRequest(f'OSRM routing failed: status {r.status_code}')
            j = r.json()
            if not j.get('routes'):
                return HttpResponseBadRequest('OSRM returned no routes')
            coords_list = j['routes'][0]['geometry']['coordinates']
            route_coords = [(c[1], c[0]) for c in coords_list]
            route_distance_miles = j['routes'][0]['distance'] * 0.000621371
        except Exception as e:
            return HttpResponseBadRequest(f'OSRM request error: {str(e)}')

        df = load_prices()
        MAX_RANGE_MILES = 500.0
        MPG = 10.0
        remaining_range = MAX_RANGE_MILES
        total_cost = 0.0
        stops = []

        sampled = [route_coords[0]]
        acc = 0.0
        for i in range(1, len(route_coords)):
            seg = haversine(route_coords[i-1], route_coords[i])
            acc += seg
            if acc >= 1.0:
                sampled.append(route_coords[i])
                acc = 0.0

        last = sampled[0]
        for pt in sampled[1:]:
            seg = haversine(last, pt)
            remaining_range -= seg
            last = pt
            if remaining_range <= 1.0:
                city,state = reverse_geocode(pt[0], pt[1])
                chosen = None
                if 'lat' in df.columns and df['lat'].notnull().any():
                    cand_geo = df[df['lat'].notnull() & df['lon'].notnull()].copy()
                    cand_geo['dist'] = cand_geo.apply(lambda r: haversine((float(r['lat']), float(r['lon'])), (pt[0], pt[1])), axis=1)
                    near = cand_geo[cand_geo['dist'] <= 30.0]
                    if not near.empty:
                        chosen = near.loc[near['Retail Price'].idxmin()]
                if chosen is None:
                    chosen = df.loc[df['Retail Price'].idxmin()]
                gallons = MAX_RANGE_MILES / MPG
                cost = gallons * float(chosen['Retail Price'])
                total_cost += cost
                stops.append({
                    'location': {'lat': pt[0], 'lon': pt[1]},
                    'station_name': chosen.get('Truckstop Name','Unknown'),
                    'address': f"{chosen.get('Address','')}, {chosen.get('City','')}, {chosen.get('State','')}" ,
                    'price_per_gallon': float(chosen['Retail Price']),
                    'gallons': round(gallons,2),
                    'estimated_cost': round(cost,2),
                    'matched_state': state
                })
                remaining_range = MAX_RANGE_MILES

        trip_fuel_gallons = route_distance_miles / MPG
        avg_price = (sum(s['price_per_gallon'] for s in stops)/len(stops)) if stops else df['Retail Price'].mean()
        trip_cost_estimate = trip_fuel_gallons * avg_price

        result = {
            'distance_miles': round(route_distance_miles,2),
            'route_points': route_coords,
            'stops': stops,
            'trip_fuel_gallons': round(trip_fuel_gallons,2),
            'trip_cost_estimate': round(trip_cost_estimate,2),
            'total_cost_by_full_refuels': round(total_cost,2)
        }
        return JsonResponse(result, json_dumps_params={'indent':2})

def demo(request):
    return render(request, 'demo.html')
