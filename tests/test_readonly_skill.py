import gc
import hashlib
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

from capability_manager.mcp_server import TOOLS, _dispatch, handle
from test_manager import Fixture, write_json


TASK = 'Need a skill to summarize meeting notes and action items'


def snapshot(directory):
    gc.collect()
    return {str(path.relative_to(directory)): (path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest())
            for path in directory.rglob('*') if path.is_file()}


class ReadOnlySkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)
        self.package = self.fixture.add('notes-skill')
        (self.package / 'SKILL.md').write_text('Read notes. READONLY-ONLY-IN-FILE', encoding='utf-8')

    def test_read_is_bounded_and_does_not_install_log_download_or_create_access(self):
        manager = self.fixture.manager()
        before = snapshot(self.fixture.root)
        with patch.object(manager.installer, 'install', side_effect=AssertionError('install called')), \
             patch.object(manager.router, 'rank', side_effect=AssertionError('remote ranking called')), \
             patch.object(manager.store, 'add_event', side_effect=AssertionError('logging called')), \
             patch('subprocess.run', side_effect=AssertionError('process started')):
            result = _dispatch(manager, 'read_static_skill', {'task': TASK})
        self.assertEqual(snapshot(self.fixture.root), before)
        self.assertEqual(result['status'], 'instructions_ready')
        self.assertIn('READONLY-ONLY-IN-FILE', result['capability']['skills'][0]['instructions'])
        self.assertEqual(result['effects'], {'packages_prepared': 0, 'permissions_created': 0,
                                            'files_written': 0, 'network_requests': 0})
        self.assertEqual(result['scope'], 'read-only-call')
        self.assertIn('추가 설치 0', result['summary_markdown'])
        self.assertEqual(manager.session_summary('chat')['counts']['active'], 0)

    def test_read_receipt_does_not_revoke_or_hide_an_earlier_preparation(self):
        manager = self.fixture.manager()
        manager.activate('notes-skill', 'existing-chat')
        before = snapshot(self.fixture.root)
        result = _dispatch(manager, 'read_static_skill', {'task': TASK})
        self.assertEqual(snapshot(self.fixture.root), before)
        self.assertIn('세션 합계는 별도', result['summary_markdown'])
        summary = manager.session_summary('existing-chat')
        self.assertEqual((summary['counts']['new_packages'], summary['counts']['active']), (1, 1))
        self.assertFalse(summary['release_completed'])
        manager.release('existing-chat')

    def test_short_context_can_rank_a_candidate_without_network_or_preparation(self):
        manager = self.fixture.manager()
        before = snapshot(self.fixture.root)
        result = _dispatch(manager, 'read_static_skill',
                           {'task': 'Need an approved skill', 'context': 'meeting notes and action items'})
        self.assertEqual(result['status'], 'instructions_ready')
        self.assertEqual(result['capability']['id'], 'notes-skill')
        self.assertEqual(snapshot(self.fixture.root), before)

    def remote_source(self):
        entry = self.fixture.entries[0]
        entry['source'] = {'type': 'git', 'url': 'https://example.com/skills.git', 'sha': 'a' * 40}
        self.fixture.write()
        policy = json.loads(self.fixture.policy.read_text())
        policy['download_hosts'] = ['example.com']
        write_json(self.fixture.policy, policy)

    def test_uncached_remote_package_never_downloads_or_falls_back_outside_policy(self):
        self.remote_source()
        manager = self.fixture.manager()
        before = snapshot(self.fixture.root)
        with patch.object(manager.installer, 'install', side_effect=AssertionError('download attempted')):
            result = _dispatch(manager, 'read_static_skill', {'task': TASK})
        self.assertEqual(result['status'], 'no_local_static_match')
        self.assertEqual(result['unavailable'], [{'id': 'notes-skill', 'reason': 'requires_preparation'}])
        self.assertEqual(snapshot(self.fixture.root), before)

    def test_retained_remote_cache_can_be_read_without_preparing_it_again(self):
        self.remote_source()
        manager = self.fixture.manager()
        cache = manager.installer.package_path(manager.catalog['notes-skill'])
        cache.parent.mkdir()
        shutil.copytree(self.package, cache)
        before = snapshot(self.fixture.root)
        result = _dispatch(manager, 'read_static_skill', {'task': TASK})
        self.assertEqual(result['instruction_source'], 'retained_cache')
        self.assertIn('캐시 재사용 1', result['summary_markdown'])
        self.assertIn('기존 보관', result['summary_markdown'])
        self.assertEqual(snapshot(self.fixture.root), before)

    def test_source_and_cache_symlinks_cannot_escape_approved_roots(self):
        outside = self.fixture.root / 'private.txt'
        outside.write_text('PRIVATE-OUTSIDE-SKILL')
        (self.package / 'SKILL.md').unlink()
        (self.package / 'SKILL.md').symlink_to(outside)
        result = _dispatch(self.fixture.manager(), 'read_static_skill', {'task': TASK})
        self.assertEqual(result['status'], 'no_local_static_match')
        self.assertNotIn('PRIVATE-OUTSIDE-SKILL', json.dumps(result))
        self.remote_source()
        manager = self.fixture.manager()
        target = manager.installer.package_path(manager.catalog['notes-skill'])
        target.parent.mkdir()
        target.symlink_to(self.package, target_is_directory=True)
        result = _dispatch(manager, 'read_static_skill', {'task': TASK})
        self.assertEqual(result['status'], 'no_local_static_match')
        self.assertNotIn('PRIVATE-OUTSIDE-SKILL', json.dumps(result))

    def test_policy_denial_and_codex_or_portable_executable_metadata_are_rejected(self):
        for manifest, document in [('.codex-plugin/plugin.json', {'hooks': 'elsewhere.json'}),
                                   ('plugin.json', {'extensions': {'com.openai': {'mcpServers': 'elsewhere.json'}}})]:
            with self.subTest(manifest=manifest):
                path = self.package / manifest
                path.parent.mkdir(exist_ok=True)
                write_json(path, document)
                result = _dispatch(self.fixture.manager(), 'read_static_skill', {'task': TASK})
                self.assertEqual(result['status'], 'no_local_static_match')
                self.assertEqual(result['unavailable'][0]['reason'], 'PermissionError')
                path.unlink()
        policy = json.loads(self.fixture.policy.read_text())
        policy['publishers'] = []
        write_json(self.fixture.policy, policy)
        result = _dispatch(self.fixture.manager(), 'read_static_skill', {'task': TASK})
        self.assertEqual(result['status'], 'no_local_static_match')

    def test_limits_routine_requests_and_storage_guards_remain_enforced(self):
        manager = self.fixture.manager()
        self.assertEqual(_dispatch(manager, 'read_static_skill', {'task': 'What is 2 + 2?'})['status'],
                         'no_local_static_match')
        with self.assertRaises(ValueError):
            _dispatch(manager, 'read_static_skill', {'task': TASK, 'expected_storage_id': '0' * 24})
        before = snapshot(self.fixture.root)
        for task in ['', 123, 'x' * 2001]:
            with self.subTest(task_type=type(task).__name__):
                with self.assertRaises(ValueError):
                    _dispatch(manager, 'read_static_skill', {'task': task})
        self.assertEqual(snapshot(self.fixture.root), before)
        (self.package / 'SKILL.md').write_text('x' * 30001)
        before = snapshot(self.fixture.root)
        with self.assertRaises(ValueError):
            _dispatch(manager, 'read_static_skill', {'task': TASK})
        self.assertEqual(snapshot(self.fixture.root), before)

    def test_initialize_provides_hook_independent_guidance_and_honest_annotations(self):
        manager = self.fixture.manager()
        manager.store.platform = 'codex'
        response = handle(manager, {'id': 1, 'method': 'initialize'})['result']
        self.assertIn('read_static_skill', response['instructions'][:512])
        self.assertIn('history is missing', response['instructions'])
        self.assertIn('read-only-call receipt does not replace', response['instructions'])
        tool = next(tool for tool in TOOLS if tool['name'] == 'read_static_skill')
        self.assertEqual(tool['annotations'], {'readOnlyHint': True, 'destructiveHint': False,
                                             'openWorldHint': False})
        for tool in TOOLS:
            if tool['name'] in ('resolve_static_skill', 'record_capability_result', 'release_capability_session'):
                self.assertIsNot(tool.get('annotations', {}).get('readOnlyHint'), True)
        codex_tools = handle(manager, {'id': 2, 'method': 'tools/list'})['result']['tools']
        self.assertIn('read_static_skill', [tool['name'] for tool in codex_tools])

    def test_claude_keeps_its_existing_tool_and_preparation_route(self):
        manager = self.fixture.manager()
        manager.store.platform = 'claude'
        response = handle(manager, {'id': 1, 'method': 'initialize'})['result']
        self.assertIn('first use resolve_static_skill', response['instructions'][:512])
        self.assertIn('real session ID', response['instructions'])
        self.assertIn('release_capability_session', response['instructions'])
        tools = handle(manager, {'id': 2, 'method': 'tools/list'})['result']['tools']
        names = [tool['name'] for tool in tools]
        self.assertNotIn('read_static_skill', names)
        self.assertIn('resolve_static_skill', names)
        self.assertEqual(len(tools), len(TOOLS) - 1)


if __name__ == '__main__':
    unittest.main()
