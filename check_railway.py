import json, urllib.request, os

token = open(os.path.expanduser('/home/pi/.railway_token')).read().strip()
API = 'https://backboard.railway.app/graphql/v2'

q = '{ service(id:"40386256-8f01-480e-9dc7-6701c4f9ec5f") { name deployments(first:3) { edges { node { status createdAt } } } } }'
data = json.dumps({'query': q}).encode()
req = urllib.request.Request(API, data=data)
req.add_header('Authorization', 'Bearer ' + token)
req.add_header('Content-Type', 'application/json')
req.add_header('User-Agent', 'Mozilla/5.0')

with urllib.request.urlopen(req, timeout=15) as r:
    result = json.loads(r.read())

svc = result.get('data', {}).get('service', {})
print('Service:', svc.get('name'))
for edge in svc.get('deployments', {}).get('edges', []):
    n = edge.get('node', {})
    print(f"  Status: {n.get('status')}  Created: {n.get('createdAt', '?')[:16]}")
