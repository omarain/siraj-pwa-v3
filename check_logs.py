import json, urllib.request, os

token = open(os.path.expanduser('/home/pi/.railway_token')).read().strip()
API = 'https://backboard.railway.app/graphql/v2'

# Get the latest deployment with logs
q = '{ service(id:"40386256-8f01-480e-9dc7-6701c4f9ec5f") { deployments(first:1) { edges { node { id status createdAt buildLogs deployLogs } } } } }'
data = json.dumps({'query': q}).encode()
req = urllib.request.Request(API, data=data)
req.add_header('Authorization', 'Bearer ' + token)
req.add_header('Content-Type', 'application/json')
req.add_header('User-Agent', 'Mozilla/5.0')

with urllib.request.urlopen(req, timeout=15) as r:
    result = json.loads(r.read())

edges = result.get('data', {}).get('service', {}).get('deployments', {}).get('edges', [])
if edges:
    node = edges[0].get('node', {})
    print(f"Status: {node.get('status')}")
    print(f"Created: {node.get('createdAt')}")
    bl = node.get('buildLogs', '')
    if bl:
        print(f"\n=== BUILD LOGS ===\n{bl[-2000:]}")
    dl = node.get('deployLogs', '')
    if dl:
        print(f"\n=== DEPLOY LOGS ===\n{dl[-2000:]}")
