import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tacit_runtime import AgentRuntime, RuntimeFailure


# Exercise actual subprocess pipes, interleaved RPC, and process cleanup.
FAKE = '''#!/usr/bin/env python3
import json, sys, time, os
session = 'session-' + str(os.getpid())
turn = 0
def emit(d):
    print(json.dumps(dict(jsonrpc='2.0', **d)), flush=True)
for line in sys.stdin:
    d = json.loads(line)
    method = d.get('method')
    if not method:
        if d.get('id') == 900:
            assert d['result']['outcome']['outcome'] == 'cancelled'
            emit(dict(id=pending, result={'stopReason': 'end_turn'}))
        continue
    if method == 'initialize': result = {'protocolVersion': 1}
    elif method == 'session/new': result = {'sessionId': session}
    elif method == 'session/set_model': result = {}
    elif method == 'session/prompt':
        turn += 1
        text = d['params']['prompt'][0]['text']
        if text == 'timeout': time.sleep(60)
        if text == 'error':
            emit(dict(id=d['id'], error={'code': -32000, 'message': 'PRIVATE_PROVIDER_DETAIL'}))
            continue
        if text == 'malformed':
            print('not json', flush=True)
            continue
        for sid, kind, body in [(session, 'agent_thought_chunk', 'hidden'), ('other', 'agent_message_chunk', 'wrong-session'), (session, 'agent_message_chunk', str(turn) + ':'), (session, 'agent_message_chunk', text)]:
            emit(dict(method='session/update', params={'sessionId':sid, 'update':{'sessionUpdate':kind,'content':{'type':'text','text':body}}}))
        if text == 'permission':
            pending = d['id']
            emit(dict(id=900, method='session/request_permission', params={}))
            continue
        result = {'stopReason': 'max_tokens' if text == 'partial' else 'end_turn'}
    else: raise AssertionError(method)
    emit(dict(id=d['id'], result=result))
'''


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / 'agent'
        self.binary.write_text(FAKE)
        self.binary.chmod(0o755)
        (self.root / 'opencode').mkdir()
        (self.root / 'opencode/auth.json').write_text(json.dumps({'openai': {'type': 'oauth'}}))
        self.env = patch.dict(os.environ, {'XDG_DATA_HOME': str(self.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def runtime(self, backend='kiro', **kwargs):
        return AgentRuntime(backend, self.root, executable=str(self.binary),
                            model='openai/test' if backend == 'opencode' else None, **kwargs)

    async def test_both_backends_and_multiturn(self):
        sessions = []
        for backend in ['kiro', 'opencode']:
            async with self.runtime(backend) as agent:
                one = await agent.prompt('한글 $(do-not-execute)')
                two = await agent.prompt('second')
                self.assertEqual(one.text, '1:한글 $(do-not-execute)')
                self.assertEqual(two.text, '2:second')
                self.assertEqual(one.session_id, two.session_id)
                self.assertEqual(one.backend, backend)
                sessions.append(one.session_id)
        self.assertNotEqual(*sessions)

    async def test_permission_request_is_rejected_without_hanging(self):
        async with self.runtime() as agent:
            result = await agent.prompt('permission')
            self.assertEqual(result.stop_reason, 'end_turn')

    async def test_partial_result_is_not_success(self):
        async with self.runtime() as agent:
            self.assertEqual((await agent.prompt('partial')).stop_reason, 'max_tokens')

    async def test_errors_close_process_and_hide_provider_details(self):
        for prompt in ['error', 'malformed']:
            async with self.runtime() as agent:
                process = agent.process
                with self.assertRaises(RuntimeFailure) as exc:
                    await agent.prompt(prompt)
                self.assertNotIn('PRIVATE_PROVIDER_DETAIL', str(exc.exception))
                self.assertIsNone(agent.process)
                self.assertIsNotNone(process.returncode)

    async def test_timeout_reaps_process(self):
        async with self.runtime(timeout=1) as agent:
            process = agent.process
            with self.assertRaisesRegex(RuntimeFailure, 'timed out'):
                await agent.prompt('timeout')
            self.assertIsNotNone(process.returncode)

    async def test_cancellation_reaps_process(self):
        async with self.runtime() as agent:
            process = agent.process
            task = asyncio.create_task(agent.prompt('timeout'))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIsNotNone(process.returncode)

    async def test_api_key_auth_does_not_silently_replace_subscription(self):
        (self.root / 'opencode/auth.json').write_text(json.dumps({'openai': {'type': 'api'}}))
        with self.assertRaisesRegex(RuntimeFailure, 'ChatGPT Plus/Pro'):
            async with self.runtime('opencode'):
                self.fail('API key must not be used')

    def test_opencode_requires_explicit_openai_model(self):
        for model in [None, 'amazon-bedrock/example']:
            with self.assertRaises(ValueError):
                AgentRuntime('opencode', self.root, model=model, executable=str(self.binary))
