#!/usr/bin/env python3
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

CONFIG = Path('/usr/local/x-ui/bin/config.json')
DB = Path('/etc/x-ui/x-ui.db')
ENV = Path('/etc/x-ui/residential-socks.env')
STATE = Path('/etc/x-ui/residential-failover-state.json')
GROUPS = Path('/etc/x-ui/client-groups.json')
POLICY = Path('/etc/x-ui/client-residential-assignments.json')
PAYMENT_DOMAINS = Path('/etc/x-ui/payment-domains.json')
FAILOVER = Path('/usr/local/bin/residential-failover.py')
GROUP_ROUTING = Path('/root/group-routing-final.py')
XRAY = Path('/usr/local/x-ui/bin/xray-linux-amd64')
STAGED_FAILOVER = Path('/tmp/strict-residential-failover-v2.py')
STAGED_GROUP_ROUTING = Path('/tmp/strict-group-routing-final-v2.py')
STAGED_POLICY = Path('/tmp/strict-client-residential-assignments-v2.json')
STAGED_PAYMENT_DOMAINS = Path('/tmp/strict-payment-domains-v2.json')
MANIFEST = Path('/etc/x-ui/strict-residential-v2-last-backup.json')
NODE = os.environ.get('NODE_ROLE', '').upper()
DRY_RUN = '--dry-run' in sys.argv
ROLLBACK = '--rollback' in sys.argv

RESIDENTIAL_TAGS = (
    'residential-r1', 'residential-r2', 'residential-r3',
    'residential-r4', 'residential-r5', 'residential-r6',
)
SAFE_TAGS = ('residential-r3', 'residential-r4', 'residential-r5', 'residential-r6')
API_DOMAINS = {
    'domain:cloud.google.com', 'domain:firebase.google.com', 'domain:googleapis.com',
    'domain:googleapi.com', 'domain:googlecloud.com', 'domain:cloud.google',
    'domain:gcr.io', 'domain:pkg.dev', 'domain:run.app', 'domain:appspot.com',
    'domain:cloudfunctions.net', 'domain:firebaseio.com', 'domain:firebaseapp.com',
}
PAYMENT_EXEMPTIONS = {
    'domain:alipay.com', 'domain:alipayobjects.com',
    'domain:pay.weixin.qq.com', 'domain:tenpay.com',
}
GOOGLE_WEB_DOMAINS = {
    'domain:google.com', 'domain:gstatic.com', 'domain:googleusercontent.com',
    'domain:ai.google.dev', 'domain:google.dev', 'domain:youtube.com',
    'domain:youtu.be', 'domain:ytimg.com', 'domain:googlevideo.com',
    'domain:ggpht.com', 'domain:gemini.google.com', 'domain:bard.google.com',
    'domain:aistudio.google.com', 'domain:makersuite.google.com',
    'domain:alkalimakersuite-pa.clients6.google.com', 'domain:deepmind.google',
    'domain:labs.google', 'domain:notebooklm.google', 'domain:workspace.google.com',
    'domain:about.google', 'domain:blog.google', 'domain:withgoogle.com',
    'domain:googleblog.com', 'domain:google.org', 'keyword:gemini',
}


def run(command, env=None, timeout=90, check=True):
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout)[-1800:]
        raise RuntimeError(f'{command[0]} failed: {detail}')
    return result


def read_json(path):
    with path.open() as handle:
        return json.load(handle)


def write_json(path, value, mode=None):
    current_mode = mode if mode is not None else (path.stat().st_mode & 0o777 if path.exists() else 0o600)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name + '.', delete=False) as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
        temp_path = Path(handle.name)
    temp_path.chmod(current_mode)
    temp_path.replace(path)


def backup(path, timestamp):
    if not path.exists():
        return None
    target = path.with_name(path.name + f'.bak-strict-v2-{timestamp}')
    shutil.copy2(path, target)
    return str(target)


def load_policy(path):
    policy = read_json(path)
    order = tuple(policy.get('failover_order') or ())
    groups = policy.get('groups') or {}
    if order != RESIDENTIAL_TAGS or set(groups) != set(RESIDENTIAL_TAGS):
        raise RuntimeError('six-group policy is incomplete')
    assignments = {}
    for outbound in RESIDENTIAL_TAGS:
        members = groups[outbound].get('members') or []
        chain_value = groups[outbound].get('failover_chain')
        if not members or len(members) != len(set(members)):
            raise RuntimeError(f'invalid members for {outbound}')
        if not isinstance(chain_value, list) or not chain_value or any(
            tag not in RESIDENTIAL_TAGS for tag in chain_value
        ):
            raise RuntimeError(f'invalid chain for {outbound}')
        for member in members:
            if member in assignments:
                raise RuntimeError(f'duplicate assignment: {member}')
            assignments[member] = outbound
    for member, outbound in (policy.get('additional_assignments') or {}).items():
        if outbound not in RESIDENTIAL_TAGS or member in assignments:
            raise RuntimeError(f'invalid additional assignment: {member}')
        assignments[member] = outbound
    if tuple(policy.get('google_r1_r2_failover_chain') or ()) != SAFE_TAGS:
        raise RuntimeError('Google chain must be R3/R4/R5/R6')
    if tuple(policy.get('payment_failover_chain') or ()) != SAFE_TAGS:
        raise RuntimeError('payment chain must be R3/R4/R5/R6')
    if set(policy.get('server_direct_domains') or []) != API_DOMAINS:
        raise RuntimeError('server direct allowlist changed')
    if set(policy.get('client_direct_domains') or []) != PAYMENT_EXEMPTIONS:
        raise RuntimeError('Alipay/WeChat exceptions changed')
    overrides = policy.get('openai_user_overrides') or {}
    if not isinstance(overrides, dict):
        raise RuntimeError('OpenAI user overrides must be an object')
    for user, override in overrides.items():
        if user not in assignments or not isinstance(override, dict):
            raise RuntimeError(f'invalid OpenAI user override: {user}')
        domains = override.get('domains')
        outbound = override.get('outbound')
        chain_value = override.get('failover_chain')
        if not isinstance(chain_value, list):
            raise RuntimeError(f'invalid OpenAI failover chain for {user}')
        chain = tuple(cast(list[str], chain_value))
        if (not isinstance(domains, list) or not domains
                or any(not isinstance(domain, str) or not domain.startswith(('domain:', 'keyword:'))
                       for domain in domains)):
            raise RuntimeError(f'invalid OpenAI domains for {user}')
        if outbound not in SAFE_TAGS or not chain or not set(chain).issubset(SAFE_TAGS):
            raise RuntimeError(f'OpenAI override must use R3/R4/R5/R6: {user}')
        if chain[0] != outbound:
            raise RuntimeError(f'OpenAI override chain must start with outbound: {user}')
    return policy, assignments


def load_existing_assignments(path):
    policy = read_json(path)
    assignments = {}
    for group in (policy.get('groups') or {}).values():
        outbound = group.get('outbound')
        if outbound is None:
            outbound = next(
                (candidate for candidate in RESIDENTIAL_TAGS
                 if group is (policy.get('groups') or {}).get(candidate)),
                None,
            )
        for member in group.get('members') or []:
            assignments[member] = outbound
    for member, outbound in (policy.get('additional_assignments') or {}).items():
        assignments[member] = outbound
    return assignments


def load_payment_domains(path):
    domains = read_json(path)
    if not isinstance(domains, list) or len(domains) != 50 or PAYMENT_EXEMPTIONS.intersection(domains):
        raise RuntimeError('payment list must contain 50 non-exempt domains')
    return domains


def wait_for_failover_idle():
    run(['systemctl', 'stop', 'residential-failover.timer'])
    deadline = time.time() + 30
    while time.time() < deadline:
        if run(['systemctl', 'is-active', 'residential-failover.service'], check=False).stdout.strip() != 'active':
            return
        time.sleep(1)
    raise RuntimeError('residential-failover.service did not become idle')


def staged_environment():
    if NODE not in {'LA', 'BWH'}:
        raise RuntimeError('NODE_ROLE must be LA or BWH')
    env = os.environ.copy()
    env.update({
        'NODE_ROLE': NODE,
        'CLIENT_RESIDENTIAL_POLICY_FILE': str(STAGED_POLICY),
        'PAYMENT_DOMAINS_FILE': str(STAGED_PAYMENT_DOMAINS),
    })
    return env


def validate_stage():
    for path in (STAGED_FAILOVER, STAGED_GROUP_ROUTING, STAGED_POLICY, STAGED_PAYMENT_DOMAINS):
        if not path.exists():
            raise RuntimeError(f'staged file missing: {path}')
    py_compile.compile(str(STAGED_FAILOVER), doraise=True)
    py_compile.compile(str(STAGED_GROUP_ROUTING), doraise=True)
    policy, assignments = load_policy(STAGED_POLICY)
    payment_domains = load_payment_domains(STAGED_PAYMENT_DOMAINS)
    live_config = read_json(CONFIG)
    live_users = {
        client['email']
        for inbound in live_config.get('inbounds', [])
        for client in (inbound.get('settings') or {}).get('clients') or []
        if client.get('email')
    }
    missing = live_users - set(assignments)
    if missing:
        raise RuntimeError('live users missing from policy: ' + ','.join(sorted(missing)))
    run([sys.executable, str(STAGED_GROUP_ROUTING), '--dry-run'], env=staged_environment())
    return policy, assignments, payment_domains


def set_env_values():
    values = {
        'CLIENT_RESIDENTIAL_POLICY_FILE': str(POLICY),
        'PAYMENT_DOMAINS_FILE': str(PAYMENT_DOMAINS),
        'PAYMENT_PRIMARY_RESIDENTIAL_TAG': 'residential-r3',
        'PAYMENT_RESIDENTIAL_CHAIN': ','.join(SAFE_TAGS),
        'GOOGLE_RESIDENTIAL_CHAIN': ','.join(SAFE_TAGS),
        'RESIDENTIAL_FAILOVER_CHAIN': ','.join(RESIDENTIAL_TAGS),
    }
    lines = ENV.read_text().splitlines()
    seen = set()
    output = []
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else ''
        if key in values:
            output.append(f'{key}={values[key]}')
            seen.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f'{key}={value}')
    mode = ENV.stat().st_mode & 0o777
    with tempfile.NamedTemporaryFile(mode='w', dir=ENV.parent, prefix=ENV.name + '.', delete=False) as handle:
        handle.write('\n'.join(output) + '\n')
        temp_path = Path(handle.name)
    temp_path.chmod(mode)
    temp_path.replace(ENV)


def update_state(assignments):
    state = read_json(STATE) if STATE.exists() else {}
    state['policy'] = 'strict-residential-v2'
    state['outbounds'] = {
        f'CLIENT_{tag.rsplit("-", 1)[-1].upper()}': tag for tag in RESIDENTIAL_TAGS
    }
    state['outbounds'].update({'GOOGLE_R1_R2': 'residential-r3', 'PAYMENT': 'residential-r3'})
    for user, override in (read_json(POLICY).get('openai_user_overrides') or {}).items():
        state['outbounds']['OPENAI_' + user.upper().replace('-', '_')] = override['outbound']
    state['client_assignments'] = assignments
    state['safe_chain'] = list(SAFE_TAGS)
    write_json(STATE, state)


def assert_config(config, assignments, payment_domains):
    rules = config.get('routing', {}).get('rules', [])
    for rule in rules:
        outbound = rule.get('outboundTag')
        if outbound in {'fallback-la', 'fallback-bwh'}:
            raise RuntimeError(f'VPS fallback route remains: {outbound}')
        if outbound == 'direct' and set(rule.get('domain') or []) - (API_DOMAINS | PAYMENT_EXEMPTIONS):
            raise RuntimeError('direct route exceeds approved exceptions')
    for outbound in RESIDENTIAL_TAGS:
        expected = {member for member, target in assignments.items() if target == outbound}
        matches = [
            rule for rule in rules
            if set(rule.get('user') or []) == expected
            and not rule.get('domain') and rule.get('outboundTag') == outbound
        ]
        if len(matches) != 1:
            raise RuntimeError(f'group route mismatch for {outbound}: {len(matches)}')
    payment_matches = [
        rule for rule in rules
        if not rule.get('user') and set(rule.get('domain') or []) == set(payment_domains)
        and rule.get('outboundTag') == 'residential-r3'
    ]
    if len(payment_matches) != 1:
        raise RuntimeError(f'payment route mismatch: {len(payment_matches)}')
    google_users = {
        member for member, target in assignments.items()
        if target in {'residential-r1', 'residential-r2'}
    }
    google_matches = [
        rule for rule in rules
        if set(rule.get('user') or []) == google_users
        and set(rule.get('domain') or []) == GOOGLE_WEB_DOMAINS
        and rule.get('outboundTag') == 'residential-r3'
    ]
    if len(google_matches) != 1:
        raise RuntimeError(f'R1/R2 Google route mismatch: {len(google_matches)}')
    overrides = read_json(POLICY).get('openai_user_overrides') or {}
    for user, override in overrides.items():
        allowed_outbounds = set(override['failover_chain'])
        matches = [
            rule for rule in rules
            if rule.get('user') == [user]
            and set(rule.get('domain') or []) == set(override['domains'])
            and rule.get('outboundTag') in allowed_outbounds
        ]
        if len(matches) != 1:
            raise RuntimeError(f'OpenAI override route mismatch for {user}: {len(matches)}')
    if not rules or rules[-1].get('outboundTag') != 'residential-r3':
        raise RuntimeError('default route is not residential-r3')


def restart_and_verify(assignments, payment_domains):
    run(['x-ui', 'restart'], timeout=45)
    time.sleep(4)
    run([str(XRAY), 'run', '-test', '-c', str(CONFIG)], timeout=30)
    if not run(['pgrep', '-f', 'xray-linux-amd64'], check=False).stdout.strip():
        raise RuntimeError('xray process missing after restart')
    assert_config(read_json(CONFIG), assignments, payment_domains)


def restore(manifest):
    wait_for_failover_idle()
    destinations = {
        'config': CONFIG, 'db': DB, 'env': ENV, 'state': STATE,
        'groups': GROUPS, 'policy': POLICY, 'payment_domains': PAYMENT_DOMAINS,
        'failover': FAILOVER, 'group_routing': GROUP_ROUTING,
    }
    for key, destination in destinations.items():
        source = manifest.get(key)
        if source:
            shutil.copy2(source, destination)
    restart_and_verify(
        manifest.get('previous_assignments', manifest['assignments']),
        manifest.get('previous_payment_domains_list', manifest['payment_domains_list']),
    )
    run(['systemctl', 'start', 'residential-failover.timer'])


def main():
    if ROLLBACK:
        if not MANIFEST.exists():
            raise SystemExit('rollback manifest not found')
        restore(read_json(MANIFEST))
        print('status=rolled_back')
        return

    policy, assignments, payment_domains = validate_stage()
    print(f'preflight=ok node={NODE} users={len(assignments)} payment_domains={len(payment_domains)}')
    if DRY_RUN:
        print('status=dry_run')
        return

    previous_assignments = load_existing_assignments(POLICY)
    previous_payment_domains = load_payment_domains(PAYMENT_DOMAINS)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    wait_for_failover_idle()
    manifest = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'node': NODE,
        'assignments': assignments,
        'payment_domains_list': payment_domains,
        'previous_assignments': previous_assignments,
        'previous_payment_domains_list': previous_payment_domains,
        'config': backup(CONFIG, timestamp),
        'db': backup(DB, timestamp),
        'env': backup(ENV, timestamp),
        'state': backup(STATE, timestamp),
        'groups': backup(GROUPS, timestamp),
        'policy': backup(POLICY, timestamp),
        'payment_domains': backup(PAYMENT_DOMAINS, timestamp),
        'failover': backup(FAILOVER, timestamp),
        'group_routing': backup(GROUP_ROUTING, timestamp),
    }
    write_json(MANIFEST, manifest, mode=0o600)
    try:
        shutil.copy2(STAGED_FAILOVER, FAILOVER)
        shutil.copy2(STAGED_GROUP_ROUTING, GROUP_ROUTING)
        shutil.copy2(STAGED_POLICY, POLICY)
        shutil.copy2(STAGED_PAYMENT_DOMAINS, PAYMENT_DOMAINS)
        FAILOVER.chmod(0o755)
        GROUP_ROUTING.chmod(0o700)
        POLICY.chmod(0o600)
        PAYMENT_DOMAINS.chmod(0o600)
        set_env_values()
        run([sys.executable, str(GROUP_ROUTING)], env=os.environ.copy())
        update_state(assignments)
        restart_and_verify(assignments, payment_domains)
        run(['systemctl', 'start', 'residential-failover.timer'])
    except Exception:
        try:
            restore(manifest)
        except Exception as rollback_error:
            print(f'CRITICAL: automatic rollback failed: {rollback_error}', file=sys.stderr)
        raise
    print(f'backup_manifest={MANIFEST}')
    print('status=applied')


if __name__ == '__main__':
    main()
