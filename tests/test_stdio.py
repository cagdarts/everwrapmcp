"""Start a real subprocess with a fictional, blocked service; no account access."""

import json
import os
import queue
import subprocess
import threading
from pathlib import Path
import sys

from mcp_types import LATEST_PROTOCOL_VERSION


def test_subprocess_tool_discovery_and_blocked_read():
    program = '''
import asyncio
from mcp.server.stdio import stdio_server
from everwrap.policy import SingleNotePolicy
from everwrap.server import build_server
from everwrap.service import SingleNoteService
class NoFetch:
    async def get_note(self, note_id):
        raise AssertionError("Offline test must never fetch a note")
async def main():
    server = build_server(SingleNoteService(
        SingleNotePolicy("11111111-1111-4111-8111-111111111111"), NoFetch()))
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
asyncio.run(main())
'''
    env = os.environ | {'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    process = subprocess.Popen([sys.executable, '-c', program], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8')
    messages = queue.Queue()
    def receive():
        for line in process.stdout:
            messages.put(json.loads(line))
    reader = threading.Thread(target=receive, daemon=True)
    reader.start()
    def send(message):
        process.stdin.write(json.dumps({'jsonrpc': '2.0'} | message) + '\n')
        process.stdin.flush()
    try:
        send({'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': LATEST_PROTOCOL_VERSION, 'capabilities': {},
            'clientInfo': {'name': 'synthetic-test', 'version': '1'}}})
        assert 'result' in messages.get(timeout=15)
        send({'method': 'notifications/initialized'})
        send({'id': 2, 'method': 'tools/list', 'params': {}})
        listing = messages.get(timeout=15)
        assert {t['name'] for t in listing['result']['tools']} == {
            'read_safe_note', 'search_safe_notes', 'semantic_search_safe_notes', 'list_safe_notebooks'
        }
        send({'id': 3, 'method': 'tools/call', 'params': {
            'name': 'read_safe_note', 'arguments': {
                'note_id': '11111111-1111-4111-8111-111111111111'}}})
        result = messages.get(timeout=15)['result']
        assert result['isError']
        assert 'Content blocked' in json.dumps(result)
        process.stdin.close()
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()
