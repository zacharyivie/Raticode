#!/usr/bin/env python3
"""Validate public protocol fixtures. Does not send messages or execute desktop work."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
root = Path(__file__).resolve().parents[1] / 'protocol' / 'v1'
validators = {}
for name in ('event', 'invitation', 'frame', 'session'):
    schema = json.loads((root / f'{name}.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    validators[name] = Draft202012Validator(schema)
count = 0
for path in sorted((root / 'fixtures').glob('*.json')):
    validators[path.stem if path.stem in ('invitation', 'frame', 'session') else 'event'].validate(json.loads(path.read_text()))
    count += 1
# Negative shape checks guard against broadening the contract accidentally.
sample = json.loads((root / 'fixtures/chat.submit.json').read_text())
for mutate in (
    lambda e: e.update(version=2), lambda e: e.update(type='shell.exec'),
    lambda e: e.update(request_id=None), lambda e: e.update(sequence=-1),
    lambda e: e['payload'].update(command='execute'),
):
    value = json.loads(json.dumps(sample)); mutate(value)
    assert not validators['event'].is_valid(value), value
print(f'{count} protocol fixtures and 5 negative cases passed; no network operations.')

# The desktop loads these files at run time; this is the only copy.
v2 = Path(__file__).resolve().parents[3] / 'src' / 'gofer' / 'devices' / 'protocol' / 'v2'
if not v2.is_dir():
    raise SystemExit(f'{v2} is missing. Run this from a full checkout of the repository.')
validators2 = {}
for name in ('event', 'invitation', 'frame', 'session'):
    schema = json.loads((v2 / f'{name}.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    validators2[name] = Draft202012Validator(schema)
count2 = 0
for path in sorted((v2 / 'fixtures').glob('*.json')):
    kind = path.stem if path.stem in ('invitation', 'frame', 'session') else 'event'
    validators2[kind].validate(json.loads(path.read_text()))
    count2 += 1
negative2 = 0
for kind, fixture, mutations in (
    ('frame', 'frame', [lambda e: e.update(index=-1), lambda e: e.update(body='AQ=='),
                        lambda e: e.update(direction='phone_to_desktop'), lambda e: e.update(extra=True)]),
    ('session', 'session', [lambda e: e.update(secret='A'*43), lambda e: e.update(mode='pair'),
                            lambda e: e.update(responder_role='controller')]),
    ('event', 'job.submit', [lambda e: e.update(thread_id=None), lambda e: e.update(type='shell.exec'),
                           lambda e: e['payload'].update(command='sh'), lambda e: e.update(request_id=None)]),
    ('event', 'fleet.snapshot', [lambda e: e['payload']['devices'][0].update(status='connected'),
                               lambda e: e['payload']['devices'][0].update(last_seen=-1)]),
    # Phone controls never carry paths, commands or unknown actions.
    ('event', 'swarm.control', [lambda e: e['payload'].update(action='delete'), lambda e: e['payload'].update(command='sh')]),
    ('event', 'approval.resolve.remote', [lambda e: e['payload'].pop('action_digest'), lambda e: e['payload'].update(decision='maybe')]),
    ('event', 'thread.snapshot.resources', [lambda e: e['payload']['resources'][0].update(command='/bin/sh'),
                                            lambda e: e['payload']['resources'][0].update(kind='shell')]),
    # Desktop control names operations from a fixed list and never a thread or path field.
    ('event', 'desktop.read', [lambda e: e['payload'].update(op='files.read'), lambda e: e.update(thread_id=e['request_id']),
                               lambda e: e['payload'].update(path='/etc/passwd')]),
    ('event', 'desktop.action', [lambda e: e['payload'].update(op='terminal.run'), lambda e: e['payload'].pop('params')]),
    # A tapped file link is a desktop action like the others, never a thread event or a read.
    ('event', 'desktop.action.files-link', [lambda e: e['payload'].update(op='files.read'), lambda e: e.update(thread_id=e['request_id']),
                                            lambda e: e.update(type='desktop.read')]),
    # The file explorer reads by project ID and relative path inside params; opening a file is an action, never a read.
    ('event', 'desktop.read.files-list', [lambda e: e['payload'].update(op='files.delete'), lambda e: e.update(thread_id=e['request_id']),
                                          lambda e: e['payload'].update(path='/etc/passwd'), lambda e: e.update(type='desktop.action')]),
    ('event', 'desktop.read.files-text', [lambda e: e['payload'].update(op='files.write'), lambda e: e.update(type='desktop.action')]),
    ('event', 'desktop.action.files-open', [lambda e: e['payload'].update(op='files.rename'), lambda e: e.update(type='desktop.read'),
                                            lambda e: e.update(thread_id=e['request_id'])]),
    ('event', 'desktop.result', [lambda e: e['payload'].update(parts=0), lambda e: e['payload'].update(body='x' * 24001),
                                 lambda e: e['payload'].update(code='crash')]),
    # Thread activity is display state: a boolean and a bounded timestamp, nothing more.
    ('event', 'sync.snapshot.activity', [lambda e: e['payload']['threads'][0].update(unread='yes'),
                                         lambda e: e['payload']['threads'][0].update(read_at='x' * 81),
                                         lambda e: e['payload']['threads'][0].update(group='unread')]),
    ('event', 'sync.request.activity', [lambda e: e['payload'].update(include_activity='true')]),
    # The Tailscale address is a plain host and port; it carries no names, keys or authority.
    ('event', 'sync.request.tailnet', [lambda e: e['payload'].update(include_tailnet_endpoint='true')]),
    ('event', 'sync.snapshot.tailnet', [lambda e: e['payload']['connection']['tailnet_endpoint'].update(auth_key='x'),
                                        lambda e: e['payload']['connection']['tailnet_endpoint'].update(port=0),
                                        lambda e: e['payload']['connection'].update(execute=True)]),
    ('event', 'approval.snapshot', [lambda e: e['payload']['approvals'][0].update(action_digest='x'),
                                    lambda e: e['payload'].pop('remote_allowed')]),
):
    original = json.loads((v2 / f'fixtures/{fixture}.json').read_text())
    for mutate in mutations:
        value = json.loads(json.dumps(original)); mutate(value)
        assert not validators2[kind].is_valid(value), (fixture, value)
        negative2 += 1
ready = {'type': 'session.ready', 'device_id': '00000000-0000-4000-8000-000000000001',
         'role': 'controller', 'capabilities': ['worker.execute'], 'initiator_topic': 'A'*43,
         'responder_topic': 'A'*43, 'trust_revision': 0}
assert not validators2['session'].is_valid(ready)
print(f'{count2} v2 fixtures and {negative2 + 1} negative cases passed; schemas do not grant authority.')
