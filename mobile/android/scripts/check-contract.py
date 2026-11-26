#!/usr/bin/env python3
"""Validate the v2 protocol schemas and fixtures. Does not send messages or execute desktop work."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
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
    # Option buttons: 2 to 8 short labels, a fixed reply setting, and a non-empty choice.
    ('event', 'approval.snapshot.choices', [lambda e: e['payload']['approvals'][0].update(options=['Only one']),
                                            lambda e: e['payload']['approvals'][0].update(options=[str(n) for n in range(9)]),
                                            lambda e: e['payload']['approvals'][0].update(reply='maybe')]),
    ('event', 'approval.resolve.choice', [lambda e: e['payload'].update(choice=''), lambda e: e['payload'].update(choice='x' * 61)]),
    ('event', 'approval.request.choices', [lambda e: e['payload'].update(include_choices='yes')]),
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
    # A workflow run carries typed inputs as named values, and no other fields.
    ('event', 'desktop.action.workflows-run-inputs', [lambda e: e['payload']['params'].update(inputs=['staging']),
                                                      lambda e: e['payload']['params'].update(command='sh'),
                                                      lambda e: e['payload']['params'].update(path='/etc/passwd')]),
    # Alert reads name switches from a fixed list, workflow messages included, and an opaque bounded position.
    ('event', 'desktop.read.rem-notices-workflow', [lambda e: e['payload']['params'].update(include=['workflow', 'workflow']),
                                                    lambda e: e['payload']['params'].update(include=['shell']),
                                                    lambda e: e['payload']['params'].update(include=[]),
                                                    lambda e: e['payload']['params'].update(mark='x' * 257)]),
    # Run again names the run only; the desktop keeps the inputs and never takes values here.
    ('event', 'desktop.action.workflows-rerun', [lambda e: e['payload']['params'].update(inputs={'token': 'x'}),
                                                 lambda e: e['payload']['params'].pop('run_id'),
                                                 lambda e: e['payload']['params'].update(path='/etc/passwd')]),
    # Turning a trigger on or off, or pausing them all, carries yes/no values and IDs only.
    ('event', 'desktop.action.workflows-trigger', [lambda e: e['payload']['params'].update(enabled='yes'),
                                                   lambda e: e['payload']['params'].update(config={'expression': '* * * * *'}),
                                                   lambda e: e['payload']['params'].pop('trigger_id')]),
    ('event', 'desktop.action.workflows-triggers-pause', [lambda e: e['payload']['params'].update(paused=1),
                                                          lambda e: e['payload']['params'].update(trigger_id='trg_1')]),
    # Self-work's account choice names a provider and an account, never a folder or command.
    ('event', 'desktop.action.selfwork-settings-accounts', [lambda e: e['payload']['params']['provider_accounts'].update(codex='../work'),
                                                            lambda e: e['payload']['params']['provider_accounts'].update({'Claude Code': 'work'}),
                                                            lambda e: e['payload']['params']['provider_accounts'].update(codex=['work']),
                                                            lambda e: e['payload']['params'].update(provider_accounts='work')]),
    # When self-work stops: bounded text and a clock time the desktop resolves, never an instant the phone worked out without its offset, a command or a folder.
    ('event', 'desktop.action.selfwork-stop', [lambda e: e['payload']['params'].update(condition='x' * 501),
                                               lambda e: e['payload']['params'].update(no_later_than={'time': '24:00'}),
                                               lambda e: e['payload']['params'].update(no_later_than={'time': '00:00', 'day': 'yesterday'}),
                                               lambda e: e['payload']['params'].update(no_later_than={'time': '00:00', 'command': 'rm'}),
                                               lambda e: e['payload']['params'].update(clear='everything'),
                                               lambda e: e['payload']['params'].update(expected_revision='3'),
                                               lambda e: e['payload']['params'].update(project='/home/me/site')]),
    # A worker's account is an account ID or "" for the default sign-in, never a name, folder or both changes at once.
    ('event', 'desktop.action.organizations-worker-account', [lambda e: e['payload']['params'].update(account='../work'),
                                                              lambda e: e['payload']['params'].update(account='work'),
                                                              lambda e: e['payload']['params'].update(paused=True),
                                                              lambda e: e['payload']['params'].pop('account'),
                                                              lambda e: e['payload']['params'].update(folder='/home')]),
    # A decision change names one decision and the revision the phone saw; adding one, or naming a project folder (even to scan it), stays on the desktop.
    ('event', 'desktop.action.rem-decision-change', [lambda e: e['payload']['params'].update(action='add'),
                                                     lambda e: e['payload']['params'].update(project='/home/me/site'),
                                                     lambda e: e['payload']['params'].update(action='scan', project='/home/me/site'),
                                                     lambda e: e['payload']['params'].pop('expected_revision'),
                                                     lambda e: e['payload']['params'].update(expected_revision='7'),
                                                     lambda e: e['payload']['params'].update(id='../D-3'),
                                                     lambda e: e['payload']['params'].update(answer='x' * 1001),
                                                     lambda e: e.update(type='desktop.read')]),
    # Replace with… and a record's Edit carry bounded text and short lists of path globs or words, and still never a project folder.
    ('event', 'desktop.action.rem-decision-replace', [lambda e: e['payload']['params'].update(project='/home/me/site'),
                                                      lambda e: e['payload']['params'].update(applies_to='src/**'),
                                                      lambda e: e['payload']['params'].update(forbids=['x'] * 11),
                                                      lambda e: e['payload']['params'].update(signals=['x' * 201]),
                                                      lambda e: e['payload']['params'].update(signals=['']),
                                                      lambda e: e['payload']['params'].update(context='x' * 1501),
                                                      lambda e: e['payload']['params'].update(check='x' * 601),
                                                      lambda e: e['payload']['params'].update(note='x' * 301),
                                                      lambda e: e['payload']['params'].update(record={'context': 'x'}),
                                                      lambda e: e['payload']['params'].pop('expected_revision')]),
    ('event', 'desktop.action.rem-decision-edit-record', [lambda e: e['payload']['params'].update(applies_to=[1]),
                                                          lambda e: e['payload']['params'].update(remember_as='record'),
                                                          lambda e: e['payload']['params'].update(record_path='docs/decisions/0001.md')]),
    # OK and Cancel on a change the user asked Rem for in chat name only the change (E-n), never its text.
    ('event', 'desktop.action.rem-decision-confirm-edit', [lambda e: e['payload']['params'].update(action='apply_edit'),
                                                          lambda e: e['payload']['params'].update(id='../E-2'),
                                                          lambda e: e['payload']['params'].update(changes=[]),
                                                          lambda e: e['payload']['params'].pop('expected_revision')]),
    ('event', 'desktop.read.rem-decisions', [lambda e: e['payload']['params'].update(project='/home/me/site')]),
    # Where work runs names a consumer, a strategy and computer IDs; the computer order and any path stay on the desktop.
    ('event', 'desktop.action.computers-place', [lambda e: e['payload']['params'].update(strategy='nearest'),
                                                 lambda e: e['payload']['params'].update(consumer='/home/me'),
                                                 lambda e: e['payload']['params'].update(computers=['../x']),
                                                 lambda e: e['payload']['params'].update(shared=['a', 'a']),
                                                 lambda e: e['payload']['params'].update(order=['a']),
                                                 lambda e: e['payload']['params'].pop('strategy')]),
    ('event', 'desktop.read.computers-placement', [lambda e: e['payload']['params'].update(consumer='rem')]),
    # Run there names one waiting run, or one organization ticket, and the suggested computer by ID; never a target, path or both kinds.
    ('event', 'desktop.action.computers-run-there', [lambda e: e['payload']['params'].pop('computer_id'),
                                                     lambda e: e['payload']['params'].update(computer_id='../x'),
                                                     lambda e: e['payload']['params'].update(organization_id='org-1', task_id='t-1'),
                                                     lambda e: e['payload']['params'].pop('run_id'),
                                                     lambda e: e['payload']['params'].update(target_id='fleet-1'),
                                                     lambda e: e['payload']['params'].update(run_id='x' * 129)]),
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
# computers.list answers in a desktop.result body: running workers by kind, and what this computer's are.
kinds = {'rem', 'phone', 'organizations', 'swarms'}
def computers_shape_ok(body):
    for c in body['computers']:
        counts, work = c.get('running_by_kind'), c.get('running_work')
        if 'running_by_kind' not in c or 'running_work' not in c:
            return False
        if counts is not None and (set(counts) - kinds or any(type(n) is not int or n < 0 for n in counts.values())
                                   or sum(counts.values()) != c['running_threads']):
            return False
        # Paired desktops share counts only; this computer lists at most 20 short labels.
        if work is not None and (not c['this_computer'] or len(work) > 20 or any(
                set(w) != {'kind', 'label'} or w['kind'] not in kinds or not 0 < len(w['label']) <= 80 for w in work)):
            return False
    return True
computers = json.loads(json.loads((v2 / 'fixtures/desktop.result.computers-list.json').read_text())['payload']['body'])
assert computers_shape_ok(computers), 'computers.list fixture body'
for mutate in (lambda b: b['computers'][1].update(running_work=[{'kind': 'rem', 'label': 'A title'}]),
               lambda b: b['computers'][0]['running_by_kind'].update(builds=1),
               lambda b: b['computers'][0]['running_by_kind'].update(rem=5),
               lambda b: b['computers'][0]['running_work'].extend([{'kind': 'rem', 'label': 'x'}] * 19),
               lambda b: b['computers'][0]['running_work'][0].update(label='x' * 81),
               lambda b: b['computers'][0]['running_work'][0].update(path='/home')):
    value = json.loads(json.dumps(computers)); mutate(value)
    assert not computers_shape_ok(value), value
    negative2 += 1
ready = {'type': 'session.ready', 'device_id': '00000000-0000-4000-8000-000000000001',
         'role': 'controller', 'capabilities': ['worker.execute'], 'initiator_topic': 'A'*43,
         'responder_topic': 'A'*43, 'trust_revision': 0}
assert not validators2['session'].is_valid(ready)
print(f'{count2} v2 fixtures and {negative2 + 1} negative cases passed; schemas do not grant authority.')
