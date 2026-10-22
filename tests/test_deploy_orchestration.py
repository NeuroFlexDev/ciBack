"""Exercise the actual shell orchestration with an isolated fake container boundary.

The fake boundary never invokes Docker, contacts the registry, or reads live config.
The shell, configuration restoration, service selection and error propagation run.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipIf(os.name == 'nt', 'Deployment shell runs on Linux')
class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.deploy = self.root/'deploy';self.deploy.mkdir()
        self.bin = self.root/'bin';self.bin.mkdir()
        self.log = self.root/'commands.jsonl'
        self.old = 'ghcr.io/neuroflexdev/ciback:sha-'+'a'*40
        self.new = 'ghcr.io/neuroflexdev/ciback:sha-'+'b'*40
        for name, text in {'.env':'TEST=true\n','.images.env':f'BACKEND_IMAGE={self.old}\n',
                           '.edge-mode':'caddy','docker-compose.yml':'new-profile',
                           'frontend-nginx.conf':'new-nginx','Caddyfile':'new-caddy'}.items():
            (self.deploy/name).write_text(text)
        script = (Path(__file__).resolve().parents[1]/'deploy/dev-platform/lernium-deploy').read_text()
        script = script.replace('/opt/lernium', str(self.root)).replace('[[ ${EUID} -eq 0 ]]','[[ 0 -eq 0 ]]')
        self.script = self.root/'deploy.sh';self.script.write_text(script)
        self.executable('docker', '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
root=Path(os.environ['TEST_ROOT']);deploy=root/'deploy'
with (root/'commands.jsonl').open('a') as log:log.write(json.dumps({'args':args,'image':(deploy/'.images.env').read_text()})+'\\n')
if args[0]=='login':sys.stdin.read();raise SystemExit(0)
if args[0]=='run':raise SystemExit(1 if os.environ.get('OLD_LACKS_MFA')=='1' else 0)
assert args.pop(0)=='compose'
while args and args[0].startswith('--'):args=args[2:]
action=args[0]
if action=='config':
 names=['backend','worker']+(['outbox'] if (deploy/'docker-compose.yml').read_text()=='new-profile' else [])
 services={name:{'image':'same-backend','restart':'unless-stopped','healthcheck':{'test':['CMD','health']}} for name in names}
 services['backend']['environment']={'MFA_REQUIRED_FOR_OPERATORS':os.environ.get('MFA_REQUIRED','false')}
 if os.environ.get('ADMIN_API')=='1':services['admin-api']={'image':'same-backend','restart':'unless-stopped','healthcheck':{'test':['CMD','health']}}
 services['frontend']={'image':'same-frontend','restart':'unless-stopped','healthcheck':{'test':['CMD','health']}}
 if os.environ.get('ADMIN_WEB')=='1':services['admin-web']={'image':'same-frontend','restart':'unless-stopped','healthcheck':{'test':['CMD','health']}}
 services['migrate']={'image':'same-backend','restart':'no'}
 print(json.dumps({'services':services}))
elif action=='ps':print('backend\\nworker')
elif action=='up' and 'admin-web' in args and os.environ.get('FAIL_ADMIN_WEB')=='1':raise SystemExit(1)
elif action=='up' and 'outbox' in args and os.environ.get('FAIL_ROLLOUT')=='1':raise SystemExit(1)
elif action=='exec':
 if 'to_regclass' in args[-1]:print('user_mfa' if os.environ.get('MFA_ENROLLED')=='1' else '')
 elif 'SELECT EXISTS' in args[-1]:print('t' if os.environ.get('MFA_ENROLLED')=='1' else 'f')
 else:print('synthetic database dump')
''')
        self.executable('curl','#!/bin/sh\nexit 0\n')
        # Only the root ownership invariant is bypassed in this isolated unprivileged CI test.
        self.executable('stat','#!/bin/sh\ncase "$2" in %u) echo 0;; %a) echo 700;; *) exit 1;; esac\n')
        self.env = {**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],'TEST_ROOT':str(self.root)}

    def executable(self,name,text):
        path=self.bin/name;path.write_text(text);path.chmod(0o755)

    def execute(self):
        return subprocess.run(['bash',str(self.script),'backend',self.new,'test-operator','https://example.test'],
                              input='test-token-not-a-secret\n',text=True,capture_output=True,env=self.env,timeout=15)

    def commands(self):return [json.loads(line)['args'] for line in self.log.read_text().splitlines()]

    def test_all_backend_services_are_pulled_and_waited(self):
        result=self.execute()
        self.assertEqual(result.returncode,0,result.stderr)
        commands=self.commands()
        pull=next(args for args in commands if 'pull' in args)
        self.assertEqual(pull[pull.index('pull')+1:],['backend','outbox','worker','migrate'])
        up=next(args for args in commands if 'up' in args and 'outbox' in args)
        self.assertIn('--wait',up)
        self.assertIn(self.new,(self.deploy/'.images.env').read_text())

    def test_admin_api_is_deployed_with_backend_workers(self):
        self.env['ADMIN_API']='1'
        result=self.execute()
        self.assertEqual(result.returncode,0,result.stderr)
        up=next(args for args in self.commands() if 'up' in args and 'admin-api' in args)
        self.assertIn('--wait',up)
        self.assertIn('worker',up)

    def frontend_execute(self):
        self.env['ADMIN_WEB']='1'
        old='ghcr.io/neuroflexdev/cifront:sha-'+'c'*40
        new='ghcr.io/neuroflexdev/cifront:sha-'+'d'*40
        with (self.deploy/'.images.env').open('a') as stream:stream.write(f'FRONTEND_IMAGE={old}\n')
        return subprocess.run(['bash',str(self.script),'frontend',new,'test-operator','https://example.test'],
                              input='test-token-not-a-secret\n',text=True,capture_output=True,env=self.env,timeout=15)

    def test_both_frontends_are_pulled_and_health_checked(self):
        result=self.frontend_execute()
        self.assertEqual(result.returncode,0,result.stderr)
        up=next(args for args in self.commands() if 'up' in args and 'admin-web' in args)
        self.assertIn('frontend',up)
        self.assertIn('--wait',up)

    def test_failed_new_admin_frontend_is_stopped_during_rollback(self):
        self.env['FAIL_ADMIN_WEB']='1'
        result=self.frontend_execute()
        self.assertNotEqual(result.returncode,0)
        commands=self.commands()
        self.assertTrue(any('stop' in args and 'admin-web' in args for args in commands))
        self.assertIn('sha-'+'c'*40,(self.deploy/'.images.env').read_text())

    def test_first_outbox_failure_restores_previous_profile_and_never_runs_it_on_old_image(self):
        rollback=self.deploy/'.rollback-profile';rollback.mkdir(mode=0o700)
        for name,text in {'docker-compose.yml':'old-profile','frontend-nginx.conf':'old-nginx','Caddyfile':'old-caddy','.pending':''}.items():
            (rollback/name).write_text(text)
        self.env['FAIL_ROLLOUT']='1'
        result=self.execute()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.deploy/'docker-compose.yml').read_text(),'old-profile')
        self.assertEqual((self.deploy/'frontend-nginx.conf').read_text(),'old-nginx')
        self.assertIn(self.old,(self.deploy/'.images.env').read_text())
        commands=self.commands()
        stopped=next(index for index,args in enumerate(commands) if 'stop' in args and 'outbox' in args)
        rollback_up=next(index for index,args in enumerate(commands) if index>stopped and 'up' in args and 'backend' in args)
        self.assertNotIn('outbox',commands[rollback_up])
        self.assertIn('--wait',commands[rollback_up])
        self.assertNotIn('Deployed backend',result.stdout)

    def test_rollback_cannot_remove_an_enrolled_second_factor(self):
        self.env.update(FAIL_ROLLOUT='1',MFA_ENROLLED='1',OLD_LACKS_MFA='1')
        result=self.execute()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Refusing rollback',result.stderr)
        self.assertIn('operator recovery required',result.stderr)
        self.assertIn(self.new,(self.deploy/'.images.env').read_text())
        commands=self.commands()
        stop=next(i for i,args in enumerate(commands) if 'stop' in args and 'backend' in args)
        probe=next(i for i,args in enumerate(commands) if args[0]=='run')
        self.assertLess(stop,probe)
        self.assertFalse(any('up' in args for args in commands[probe+1:]))

    def test_rollback_cannot_remove_operator_enforcement_before_enrollment(self):
        self.env.update(FAIL_ROLLOUT='1',MFA_REQUIRED='true',OLD_LACKS_MFA='1')
        result=self.execute()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Refusing rollback',result.stderr)
        self.assertIn(self.new,(self.deploy/'.images.env').read_text())


if __name__=='__main__':unittest.main()
