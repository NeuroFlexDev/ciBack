"""Configure an ignored local .env from a credential file without echoing secrets."""
import argparse,os,secrets
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--credentials',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
target=root/'.env'
existing={}
if target.exists():
    for line in target.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);existing[key]=value
password=existing.get('POSTGRES_PASSWORD') or secrets.token_urlsafe(24)
existing.setdefault('ENV','dev')
existing.setdefault('DEBUG','false')
existing.setdefault('POSTGRES_USER','ciuser')
existing.setdefault('POSTGRES_DB','cidb')
existing.setdefault('POSTGRES_PASSWORD',password)
existing.setdefault('DATABASE_URL',f"postgresql+psycopg2://{existing['POSTGRES_USER']}:{password}@localhost:5432/{existing['POSTGRES_DB']}")
existing.setdefault('JWT_SECRET',secrets.token_urlsafe(48))
existing['VSELLM_API_KEY']=args.credentials.read_text().strip()
existing.setdefault('VSELLM_BASE_URL','https://api.vsellm.ru/v1')
fd=os.open(target,os.O_CREAT|os.O_WRONLY|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as handle:
    handle.write('\n'.join(f'{key}={value}' for key,value in existing.items())+'\n')
os.chmod(target,0o600)
print('Configured ignored local .env; secrets were not printed.')
