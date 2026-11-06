"""Selection, installation boundaries and fail-closed language coverage."""
import builtins
import os
import json
import stat
import sys
from types import SimpleNamespace

import pytest
from everwrap.policy import SingleNotePolicy, InvalidPolicy
from everwrap.setup import save_languages, main
from everwrap.redaction import get_redactor

@pytest.mark.parametrize('value', [[], ['it'], ['en','en'], 'en', None, [42]])
def test_invalid_language_policy(tmp_path, value):
    path = tmp_path/'policy.json'
    path.write_text(json.dumps({'access_mode':'denylist', 'languages':value}))
    with pytest.raises(InvalidPolicy):
        SingleNotePolicy.from_file(path)

@pytest.mark.parametrize('languages', [('en',), ('tr',), ('en','tr')])
def test_selected_packs_and_unsupported_passages(languages):
    redactor = get_redactor(False, languages)
    examples = {'en': 'I spoke with Alex Smith about the community garden.',
                'tr': 'Bugün Ayşe Yılmaz ile bahçede konuştum.'}
    for code, text in examples.items():
        output = redactor.sanitize_text(text)
        assert all(name not in output for name in ('Alex', 'Smith', 'Ayşe', 'Yılmaz'))
        if code not in languages:
            assert 'LANGUAGE_UNSUPPORTED' in output or '[REDACTED]' in output
        else:
            assert '[PERSON]' in output
    for text in ('Je voudrais parler de mon travail avec mes collègues.',
                 'Quiero hablar sobre mi trabajo con mis compañeros.'):
        assert redactor.sanitize_text(text) == '[LANGUAGE_UNSUPPORTED]'
    assert redactor.sanitize_text('2026-09-17') == '2026-09-17'


def test_policy_update_preserves_private_settings(tmp_path):
    path = tmp_path/'policy.json'
    data = {'access_mode':'denylist', 'content_mode':'redacted', 'mask_dates':False,
            'blocked_note_ids':['11111111-2222-3333-4444-555555555555']}
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    assert SingleNotePolicy.from_file(path).languages == ('en','tr')
    save_languages(path, ('en',), original)
    assert json.loads(path.read_text()) == data | {'languages':['en']}
    if os.name != 'nt':
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        save_languages(path, ('tr',), original)
    assert SingleNotePolicy.from_file(path).languages == ('en',)

@pytest.mark.parametrize('languages, forbidden', [(('en',), ('transformers','torch')), (('tr',), ('en_core_web_md', 'en_core_web_lg'))])
def test_disabled_pack_is_not_imported(monkeypatch, languages, forbidden):
    from everwrap.redaction import PresidioRedactor
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split('.')[0] in forbidden:
            raise AssertionError('Disabled pack imported')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    PresidioRedactor(languages)

@pytest.mark.parametrize('fail_smoke', [False, True])
def test_setup_installs_only_selected_pack_and_commits_after_smoke(tmp_path, monkeypatch, fail_smoke):
    from everwrap import setup, redaction, language
    path = tmp_path/'policy.json'
    path.write_text('{"access_mode":"denylist","mask_dates":false}')
    original = path.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['setup', '--languages', 'en', '--policy', str(path)])
    monkeypatch.setattr(setup.shutil, 'which', lambda _: '/fake/uv')
    calls = []
    monkeypatch.setattr(setup.subprocess, 'run', lambda command, **kw: calls.append(command))
    monkeypatch.setattr(language, 'setup_model', lambda: pytest.fail('Turkish download in English setup'))
    monkeypatch.setattr(redaction, 'get_redactor', lambda *args: SimpleNamespace(
        sanitize_text=lambda _: 'Alex Smith' if fail_smoke else 'Talk to [PERSON] about the community garden.'))
    assert main() == (1 if fail_smoke else 0)
    assert len(calls) == 1 and len(calls[0]) == 6
    assert 'en_core_web_md' in calls[0][-1]
    if fail_smoke:
        assert path.read_bytes() == original
    else:
        assert SingleNotePolicy.from_file(path).languages == ('en',)
        assert SingleNotePolicy.from_file(path).mask_dates is False


def test_language_change_during_read_withholds_response(tmp_path):
    import asyncio
    from everwrap.live import ConfiguredService
    from everwrap.service import ProcessingBlocked
    from tests.test_live import Backend, ALLOWED
    path = tmp_path/'policy.json'
    config = {'access_mode':'denylist', 'content_mode':'unredacted', 'languages':['en']}
    path.write_text(json.dumps(config))
    backend = Backend()
    original = backend.get_note
    async def change(note_id):
        path.write_text(json.dumps(config | {'languages':['tr']}))
        return await original(note_id)
    backend.get_note = change
    with pytest.raises(ProcessingBlocked):
        asyncio.run(ConfiguredService(path, lambda _: backend).read_safe_note(ALLOWED))


def test_missing_pack_blocks_before_upstream_access(tmp_path, monkeypatch):
    import asyncio
    from everwrap import redaction
    from everwrap.live import ConfiguredService
    from tests.test_live import Backend, ALLOWED
    path = tmp_path/'policy.json'
    path.write_text(json.dumps({'access_mode':'denylist', 'content_mode':'redacted', 'languages':['en']}))
    backend = Backend()
    def missing(mask_dates, languages):
        assert languages == ('en',)
        raise OSError('Missing selected model')
    monkeypatch.setattr(redaction, 'get_redactor', missing)
    with pytest.raises(OSError):
        asyncio.run(ConfiguredService(path, lambda _:backend).read_safe_note(ALLOWED))
    assert backend.reads == []
