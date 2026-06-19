import os
f = open(os.path.expanduser('~/.siraj/supabase.env'), 'rb')
raw = f.read().decode()
f.close()
for line in raw.strip().split('\n'):
    if line.startswith('SUPABASE_SERVICE_ROLE_KEY='):
        print('SUPABASE_SERVICE_ROLE_KEY=' + line.split('=', 1)[1])
        break
