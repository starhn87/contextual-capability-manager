import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import subprocess
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from capability_manager.codex_catalog import parse_listing
from capability_manager.manager import CapabilityManager
from capability_manager.mcp_server import handle
from capability_manager.runtime import storage_id


FAKE_CODEX = '''#!{python}
import json,sys
from pathlib import Path
state=Path({state!r})
d=json.loads(state.read_text())
args=sys.argv[1:]
with Path({calls!r}).open('a') as f: f.write(json.dumps(args)+'\\n')
if args==['plugin','list','--available','--json']:
 print(json.dumps(d))
elif args==['plugin','add','linear@test','--json']:
 if d.get('fail'): sys.exit(1)
 if not d.get('unconfirmed'):
  d['installed']=[dict(d['available'][0],installed=True,enabled=True,version='2.1.0')]
  state.write_text(json.dumps(d))
 print(json.dumps({{'pluginId':'linear@test','installedPath':'/not-used'}}))
else: sys.exit(2)
'''


class ConnectorFixture:
    def __init__(self):
        self.clients, self.tokens = {}, set()
        self.wide_scope = self.no_tools = self.redirect = False
        self.echo_token = False
        self.list_count = self.writes = 0
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, result, status=200):
                data = json.dumps(result).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.startswith('/.well-known/oauth-protected-resource'):
                    self.send({'resource': fixture.url + '/mcp', 'authorization_servers': [fixture.url]})
                elif self.path == '/.well-known/oauth-authorization-server':
                    self.send({'issuer': fixture.url, 'authorization_endpoint': fixture.url + '/authorize',
                        'token_endpoint': fixture.url + '/token', 'registration_endpoint': fixture.url + '/register',
                        'code_challenge_methods_supported': ['S256'],
                        'authorization_response_iss_parameter_supported': True})
                else:
                    self.send({}, 404)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                if self.path == '/register':
                    if fixture.redirect:
                        self.send_response(302)
                        self.send_header('Location', 'http://127.0.0.1:1/steal')
                        self.end_headers()
                        return
                    data = json.loads(raw)
                    identity = 'client-' + str(len(fixture.clients))
                    fixture.clients[identity] = data
                    self.send({'client_id': identity})
                    return
                if self.path == '/token':
                    data = parse_qs(raw.decode())
                    client = fixture.clients.get(data.get('client_id', [''])[0])
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(data.get('code_verifier', [''])[0].encode()).digest()).rstrip(b'=').decode()
                    if (not client or data.get('redirect_uri') != client['redirect_uris']
                            or data.get('resource') != [fixture.url + '/mcp'] or data.get('code') != [challenge]):
                        self.send({'error': 'invalid_grant'}, 400)
                        return
                    token = 'PRIVATE-TOKEN-' + str(len(fixture.tokens))
                    fixture.tokens.add(token)
                    self.send({'access_token': token, 'token_type': 'Bearer', 'expires_in': 3600,
                               'scope': 'read write' if fixture.wide_scope else 'read'})
                    return
                if self.path == '/mcp':
                    if self.headers.get('Authorization', '')[7:] not in fixture.tokens:
                        self.send({'error': 'auth_required'}, 401)
                        return
                    request = json.loads(raw)
                    if 'id' not in request:
                        self.send({})
                        return
                    method = request['method']
                    if method == 'initialize':
                        result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fixture', 'version': '1'}}
                    elif method == 'tools/list':
                        fixture.list_count += 1
                        if fixture.no_tools:
                            result = {'tools': []}
                        elif not request.get('params', {}).get('cursor'):
                            result = {'tools': [{'name': 'delete', 'inputSchema': {'type': 'object'}}], 'nextCursor': 'read-page'}
                        else:
                            result = {'tools': [{'name': 'lookup', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}}]}
                    elif method == 'tools/call':
                        fixture.writes += request['params']['name'] == 'delete'
                        result = {'content': [{'type': 'text', 'text': 'ISSUE-1 priority=2'}]}
                        if fixture.echo_token:
                            result['content'][0]['text'] += ' ' + self.headers.get('Authorization', '')[7:]
                    else:
                        result = {}
                    self.send({'jsonrpc': '2.0', 'id': request['id'], 'result': result})
                    return
                self.send({}, 404)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def callback(self, view, **overrides):
        params = parse_qs(urlparse(view['authorization_url']).query)
        callback = {'state': params['state'][0], 'code': params['code_challenge'][0], 'iss': self.url}
        callback.update(overrides)
        with urlopen(params['redirect_uri'][0] + '?' + urlencode(callback), timeout=3) as response:
            return response.status


class NativeSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.connector = ConnectorFixture()
        self.addCleanup(self.connector.close)
        self.state, self.calls = self.root / 'host.json', self.root / 'host-calls.jsonl'
        self.host = {'available': [{'name': 'linear', 'marketplaceName': 'test', 'pluginId': 'linear@test',
                     'installed': False, 'installPolicy': 'AVAILABLE', 'version': '2.1.0'}], 'installed': []}
        self.state.write_text(json.dumps(self.host))
        binary = self.root / 'codex'
        binary.write_text(FAKE_CODEX.format(python=sys.executable, state=str(self.state), calls=str(self.calls)))
        binary.chmod(0o700)
        self.env = patch.dict(os.environ, {'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
            'CAPMGR_PLATFORM': 'codex', 'CAPMGR_DECIDER_URL': '', 'CAPMGR_STORE_DECISION_TEXT': '0'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.entry = {'id': 'linear-test', 'name': 'Linear', 'description': 'Search project issues',
            'kind': 'plugin', 'publisher': 'test', 'version': '0', 'tags': ['linear', 'issues'],
            'source': {'type': 'native', 'plugin_name': 'linear', 'marketplace': 'test',
                'install_policy': 'AVAILABLE', 'connection': {'url': self.connector.url + '/mcp',
                    'server_name': 'linear', 'scopes': ['read']}}, 'permissions': {}}
        self.catalog, self.policy = self.root / 'catalog.json', self.root / 'policy.json'
        self.catalog.write_text(json.dumps({'capabilities': [self.entry]}))
        self.policy_data = {'kinds': ['plugin'], 'native_marketplaces': ['test'],
            'connector_hosts': ['127.0.0.1'], 'oauth_hosts': ['127.0.0.1'],
            'allowed_read_tools': ['linear-test:lookup']}
        self.policy.write_text(json.dumps(self.policy_data))
        self.manager = self.make_manager()
        self.addCleanup(lambda: self.manager.release('chat'))

    def make_manager(self):
        return CapabilityManager([self.catalog], self.policy, self.root / 'data',
                                 include_codex_catalog=False, include_claude_catalog=False)

    def start(self):
        result = self.manager.resolve('내 Linear 프로젝트 이슈를 조회할 커넥터가 필요해', 'chat')
        self.assertEqual(result['status'], 'awaiting_auth')
        return result['capability']

    def ready(self):
        view = self.start()
        self.connector.callback(view)
        result = self.manager.resume_setup('chat', view['setup_id'], wait_seconds=1)
        self.assertEqual(result['availability'], 'ready')
        return result

    def test_real_cli_oauth_gateway_resume_and_receipt(self):
        view = self.start()
        self.assertEqual(self.manager.session_summary('chat')['counts']['new_packages'], 1)
        self.assertEqual(self.manager.session_summary('chat')['counts']['active'], 0)
        self.assertEqual(view['setup_id'], self.manager.activate('linear-test', 'chat')['setup_id'])
        self.connector.callback(view)
        ready = self.manager.resume_setup('chat', view['setup_id'], wait_seconds=1)
        self.assertEqual(ready['resume']['action'], 'continue_original_task')
        self.assertEqual([t['name'] for t in ready['mcp_servers']['linear']], ['lookup'])
        self.assertEqual(self.connector.list_count, 2)
        result = self.manager.invoke('chat', 'linear-test', 'linear', 'lookup', {'query': 'performance'})
        self.assertIn('ISSUE-1', result['content'][0]['text'])
        with self.assertRaises(ValueError):
            self.manager.invoke('chat', 'linear-test', 'linear', 'delete')
        self.assertEqual(self.connector.writes, 0)
        self.manager.record_outcome('chat', 'linear-test', success=True)
        flow = self.manager.native_flows[view['setup_id']]
        receipt = self.manager.release('chat')
        self.assertIsNone(flow.token)
        self.assertEqual(receipt['session_summary']['counts']['active'], 0)
        self.assertIn('네이티브 설치 유지', receipt['summary_markdown'])
        self.assertIn('성공 보고', receipt['summary_markdown'])
        self.assertEqual(len([line for line in self.calls.read_text().splitlines() if '"add"' in line]), 1)
        for path in (self.root / 'data').rglob('*'):
            if path.is_file():
                self.assertNotIn(b'PRIVATE-TOKEN', path.read_bytes())
                self.assertNotIn(view['authorization_url'].encode(), path.read_bytes())
        self.assertEqual(self.manager.resume_setup('chat', view['setup_id'])['availability'], 'cancelled')

    def test_session_guard_wrong_state_issuer_and_replay(self):
        view = self.start()
        with self.assertRaises(ValueError):
            self.manager.resume_setup('other-chat', view['setup_id'])
        with self.assertRaises(HTTPError) as error:
            self.connector.callback(view, state='forged')
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.manager.resume_setup('chat', view['setup_id'])['availability'], 'awaiting_auth')
        self.connector.callback(view, iss='https://evil.example')
        self.assertEqual(self.manager.resume_setup('chat', view['setup_id'])['availability'], 'authentication_failed')
        with self.assertRaises(HTTPError) as error:
            self.connector.callback(view)
        self.assertEqual(error.exception.code, 410)

    def test_denied_auth_retry_and_scope_escalation(self):
        view = self.start()
        self.connector.callback(view, error='access_denied')
        self.assertEqual(self.manager.resume_setup('chat', view['setup_id'])['availability'], 'authentication_failed')
        retry = self.manager.resume_setup('chat', view['setup_id'], retry=True)
        self.assertNotEqual(view['authorization_url'], retry['authorization_url'])
        self.connector.wide_scope = True
        self.connector.callback(retry)
        self.assertEqual(self.manager.resume_setup('chat', view['setup_id'])['availability'], 'authentication_failed')
        self.assertFalse(self.manager.store.is_active('chat', 'linear-test'))

    def test_restart_expires_pending_and_retries_without_reinstalling(self):
        view = self.start()
        self.manager.native_flows.pop(view['setup_id']).close()
        restarted = self.make_manager()
        self.addCleanup(lambda: restarted.release('chat'))
        self.assertEqual(restarted.resume_setup('chat', view['setup_id'])['availability'], 'expired')
        retry = restarted.resume_setup('chat', view['setup_id'], retry=True)
        self.connector.callback(retry)
        self.assertEqual(restarted.resume_setup('chat', view['setup_id'])['availability'], 'ready')
        self.assertEqual(len([line for line in self.calls.read_text().splitlines() if '"add"' in line]), 1)

    def test_no_tools_does_not_report_connection_ready(self):
        view = self.start()
        self.connector.no_tools = True
        self.connector.callback(view)
        result = self.manager.resume_setup('chat', view['setup_id'])
        self.assertEqual(result['availability'], 'requires_review')
        self.assertEqual(self.manager.session_summary('chat')['counts']['active'], 0)

    def test_policy_revocation_blocks_existing_tool_call(self):
        self.ready()
        self.policy_data['native_marketplaces'] = []
        self.policy.write_text(json.dumps(self.policy_data))
        with self.assertRaises(PermissionError):
            self.manager.invoke('chat', 'linear-test', 'linear', 'lookup')

    def test_host_disable_during_login_prevents_ready(self):
        view = self.start()
        host = json.loads(self.state.read_text())
        host['installed'][0]['enabled'] = False
        self.state.write_text(json.dumps(host))
        self.connector.callback(view)
        self.assertEqual(self.manager.resume_setup('chat', view['setup_id'])['availability'], 'requires_review')
        self.assertFalse(self.manager.store.is_active('chat', 'linear-test'))

    def test_rejected_credential_restarts_auth_without_reinstallation(self):
        ready = self.ready()
        self.connector.tokens.clear()
        result = self.manager.invoke('chat', 'linear-test', 'linear', 'lookup')
        self.assertEqual(result['error'], 'authentication_required')
        self.assertEqual(result['capability_setup']['availability'], 'awaiting_auth')
        self.assertFalse(self.manager.store.is_active('chat', 'linear-test'))
        self.connector.callback(result['capability_setup'])
        self.assertEqual(self.manager.resume_setup('chat', ready['setup_id'])['availability'], 'ready')
        self.assertEqual(len([line for line in self.calls.read_text().splitlines() if '"add"' in line]), 1)

    def test_other_process_release_cancels_pending_callback(self):
        view = self.start()
        other = self.make_manager()
        other.release('chat')
        with self.assertRaises((HTTPError, OSError)):
            self.connector.callback(view)
        self.assertEqual(self.connector.tokens, set())
        flow = self.manager.native_flows[view['setup_id']]
        self.assertEqual(flow.status(), 'expired')
        self.assertIsNone(flow.token)

    def test_cli_keeps_callback_alive_then_reports_released_verification(self):
        process = subprocess.Popen([sys.executable, '-m', 'capability_manager.cli',
            '--catalog', str(self.catalog), '--policy', str(self.policy), '--data-dir', str(self.root / 'cli-data'),
            'resolve', 'Need Linear connector', '--session', 'cli-chat'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        line = process.stderr.readline()
        self.assertTrue(line.startswith('계정 로그인: '), line)
        self.connector.callback({'authorization_url': line.strip().split(': ', 1)[1]})
        stdout, stderr = process.communicate(timeout=6)
        self.assertEqual(process.returncode, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result['availability'], 'verified_then_released')
        self.assertEqual(result['resume']['action'], 'use_persistent_mcp_session')

    def test_native_without_adapter_is_not_claimed_connected(self):
        self.entry['source'].pop('connection')
        self.catalog.write_text(json.dumps({'capabilities': [self.entry]}))
        result = self.manager.resolve('Need Linear connector', 'chat')
        self.assertEqual(result['status'], 'requires_host_activation')
        self.assertEqual(self.manager.session_summary('chat')['counts']['new_packages'], 1)
        self.assertEqual(self.manager.session_summary('chat')['counts']['active'], 0)

    def test_credentials_are_redacted_from_remote_tool_results(self):
        self.ready()
        self.connector.echo_token = True
        result = self.manager.invoke('chat', 'linear-test', 'linear', 'lookup')
        self.assertNotIn('PRIVATE-TOKEN', json.dumps(result))
        self.assertIn('[credential redacted]', result['content'][0]['text'])

    def test_pending_auth_expiry_and_callback_cannot_activate(self):
        view = self.start()
        flow = self.manager.native_flows[view['setup_id']]
        flow.created_at -= flow.ttl + 1
        self.assertEqual(self.manager.resume_setup('chat', view['setup_id'])['availability'], 'expired')
        with self.assertRaises((HTTPError, OSError)):
            self.connector.callback(view)
        self.assertFalse(self.manager.store.is_active('chat', 'linear-test'))

    def test_service_configuration_change_blocks_existing_connection(self):
        ready = self.ready()
        self.entry['source']['connection']['scopes'] = ['write']
        self.catalog.write_text(json.dumps({'capabilities': [self.entry]}))
        with self.assertRaises(PermissionError):
            self.manager.invoke('chat', 'linear-test', 'linear', 'lookup')
        with self.assertRaises(PermissionError):
            self.manager.resume_setup('chat', ready['setup_id'])

    def test_mcp_storage_guard_prevents_install_and_resume(self):
        def call(name, arguments):
            result = handle(self.manager, {'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})['result']
            return result, json.loads(result['content'][0]['text'])
        arguments = {'task': 'Need Linear connector', 'session_id': 'chat', 'expected_storage_id': '0' * 24}
        result, _ = call('resolve_capability', arguments)
        self.assertTrue(result['isError'])
        self.assertFalse(self.calls.exists())
        arguments['expected_storage_id'] = storage_id(self.root / 'data')
        _, result = call('resolve_capability', arguments)
        view = result['capability']
        self.connector.callback(view)
        _, result = call('resume_capability_setup', {'session_id': 'chat', 'setup_id': view['setup_id'],
                         'expected_storage_id': storage_id(self.root / 'data')})
        self.assertEqual(result['availability'], 'ready')

    def test_host_denial_and_unconfirmed_install_are_not_success(self):
        self.host['available'][0]['installPolicy'] = 'NOT_AVAILABLE'
        self.state.write_text(json.dumps(self.host))
        self.assertEqual(self.manager.resolve('Need Linear connector', 'chat')['status'], 'requires_review')
        self.assertNotIn('"add"', self.calls.read_text())
        self.manager.release('chat')
        self.host['available'][0]['installPolicy'] = 'AVAILABLE'
        self.host['unconfirmed'] = True
        self.state.write_text(json.dumps(self.host))
        self.assertEqual(self.manager.resolve('Need Linear connector', 'chat')['status'], 'installation_failed')
        self.assertEqual(self.manager.session_summary('chat')['counts']['new_packages'], 0)

    def test_installed_native_reuse_and_disabled_host(self):
        self.host['installed'] = [dict(self.host['available'][0], installed=True, enabled=True)]
        self.state.write_text(json.dumps(self.host))
        self.start()
        self.assertNotIn('"add"', self.calls.read_text())
        self.assertEqual(self.manager.session_summary('chat')['counts']['native_reused'], 1)
        self.manager.release('chat')
        self.host['installed'][0]['enabled'] = False
        self.state.write_text(json.dumps(self.host))
        self.assertEqual(self.manager.resolve('Need Linear connector', 'chat')['status'], 'requires_review')

    def test_oauth_redirect_is_not_followed(self):
        self.connector.redirect = True
        self.assertEqual(self.manager.resolve('Need Linear connector', 'chat')['status'], 'authentication_failed')
        self.assertEqual(self.connector.clients, {})

    def test_named_blocked_service_cannot_select_unrelated_plugin(self):
        unrelated = dict(self.entry, id='moodys', name="Moody's", description='Search issues in user projects read only',
                         source={'type': 'native', 'plugin_name': 'moodys', 'marketplace': 'test', 'install_policy': 'AVAILABLE'})
        self.entry['source']['marketplace'] = 'blocked'
        self.catalog.write_text(json.dumps({'capabilities': [self.entry, unrelated]}))
        result = self.manager.search('Need a Linear connector to search user project issues read only', session_id='chat')
        self.assertIsNone(result['recommendation'])
        self.assertEqual(result['candidates'], [])

    def test_curated_linear_preserves_install_policy_and_installed_reuse(self):
        document = {'available': [{'name': 'linear', 'marketplaceName': 'openai-curated-remote',
            'installed': False, 'installPolicy': 'NOT_AVAILABLE'}], 'installed': []}
        entry = next(iter(parse_listing(document).values()))
        self.assertEqual(entry.source['connection']['scopes'], ['read'])
        self.assertEqual(entry.source['install_policy'], 'NOT_AVAILABLE')
        document['available'] = []
        document['installed'] = [{'name': 'linear', 'marketplaceName': 'openai-curated-remote',
            'installed': True, 'enabled': True, 'installPolicy': 'AVAILABLE', 'version': '1'}]
        self.assertTrue(next(iter(parse_listing(document).values())).source['installed'])


if __name__ == '__main__':
    unittest.main()
