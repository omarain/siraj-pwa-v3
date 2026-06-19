import json, urllib.request, os, time

token = open(os.path.expanduser('/home/pi/.railway_token')).read().strip()
API = 'https://backboard.railway.app/graphql/v2'

def gql(query, variables=None):
    payload = {'query': query}
    if variables: payload['variables'] = variables
    data = json.dumps(payload).encode()
    req = urllib.request.Request(API, data=data)
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'Mozilla/5.0')
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

env_id = 'd816ecef-94be-4010-a36b-4b14727cd07f'
svc_id = '40386256-8f01-480e-9dc7-6701c4f9ec5f'

# Trigger fresh deploy
print("Deploying...")
r = gql(f'mutation {{ serviceInstanceDeploy(environmentId:"{env_id}", serviceId:"{svc_id}", latestCommit: true) }}')
print(r)

# Wait for it
time.sleep(5)

# Check deployments
q = '{ service(id:"' + svc_id + '") { deployments(first:2) { edges { node { id status createdAt } } } } }'
r2 = gql(q)
for e in r2.get('data',{}).get('service',{}).get('deployments',{}).get('edges',[]):
    n = e['node']
    print(f"  {n['status']}  {n['createdAt'][:19]}")
