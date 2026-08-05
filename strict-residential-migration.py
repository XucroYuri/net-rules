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
STAGED_FAILOVER = Path('/tmp/strict-residential-failover.py')
STAGED_GROUP_ROUTING = Path('/tmp/strict-group-routing-final.py')
STAGED_POLICY = Path('/tmp/strict-client-residential-assignments.json')
STAGED_PAYMENT_DOMAINS = Path('/tmp/strict-payment-domains.json')
MANIFEST = Path('/etc/x-ui/strict-residential-last-backup.json')
STRICT_CHAIN = ['residential-r3', 'residential-r4', 'residential-r5']
DIRECT_DOMAINS = {
    'domain:cloud.google.com', 'domain:firebase.google.com', 'domain:googleapis.com',
    'domain:googleapi.com', 'domain:googlecloud.com', 'domain:cloud.google',
    'domain:gcr.io', 'domain:pkg.dev', 'domain:run.app', 'domain:appspot.com',
    'domain:cloudfunctions.net', 'domain:firebaseio.com', 'domain:firebaseapp.com',
}
PAYMENT_EXEMPTIONS = {
    'domain:alipay.com', 'domain:alipayobjects.com',
    'domain:pay.weixin.qq.com', 'domain:tenpay.com',
}
DRY_RUN = '--dry-run' in sys.argv
ROLLBACK = '--rollback' in sys.argv
NODE = os.environ.get('NODE_ROLE', '').upper()


def run(command, env=None, timeout=60, check=True):
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout)[-1600:]
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
    target = path.with_name(path.name + f'.bak-strict-{timestamp}')
    shutil.copy2(path, target)
    return str(target)


def load_policy(path):
    policy = read_json(path)
    if policy.get('failover_order') != STRICT_CHAIN:
        raise RuntimeError('policy failover order is not R3/R4/R5')
    groups = policy.get('groups') or {}
    if set(groups) != set(STRICT_CHAIN):
        raise RuntimeError('policy group set is incomplete')
    assignments = {}
    for outbound in STRICT_CHAIN:
        members = groups[outbound].get('members') or []
        if not members or len(members) != len(set(members)):
            raise RuntimeError(f'invalid members for {outbound}')
        for member in members:
            if member in assignments:
                raise RuntimeError(f'duplicate policy assignment: {member}')
            assignments[member] = outbound
    for member, outbound in (policy.get('additional_assignments') or {}).items():
        if outbound not in STRICT_CHAIN or member in assignments:
            raise RuntimeError(f'invalid additional assignment: {member}')
        assignments[member] = outbound
    if set(policy.get('server_direct_domains') or []) != DIRECT_DOMAINS:
        raise RuntimeError('server direct allowlist changed unexpectedly')
    if set(policy.get('client_direct_domains') or []) != PAYMENT_EXEMPTIONS:
        raise RuntimeError('client payment exemptions changed unexpectedly')
    return policy, assignments


def load_payment_domains(path):
    domains = read_json(path)
    if not isinstance(domains, list) or len(domains) != 50 or PAYMENT_EXEMPTIONS.intersection(domains):
        raise RuntimeError('payment list must contain 50 domains and exclude Alipay/WeChat')
    return domains


def load_template():
    connection = sqlite3.connect(DB)
    try:
        row = connection.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    finally:
        connection.close()
    if not row or not row[0]:
        raise RuntimeError('xrayTemplateConfig not found')
    return json.loads(row[0])


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
    live_users = set()
    live_config = read_json(CONFIG)
    for inbound in live_config.get('inbounds', []):
        for client in (inbound.get('settings') or {}).get('clients') or []:
            if client.get('email'):
                live_users.add(client['email'])
    missing = live_users - set(assignments)
    if missing:
        raise RuntimeError('live users missing from policy: ' + ','.join(sorted(missing)))
    run([sys.executable, str(STAGED_GROUP_ROUTING), '--dry-run'], env=staged_environment(), timeout=60)
    return policy, assignments, payment_domains


def set_env_values():
    values = {
        'CLIENT_RESIDENTIAL_POLICY_FILE': str(POLICY),
        'PAYMENT_RESIDENTIAL_CHAIN': ','.join(STRICT_CHAIN),
        'RESIDENTIAL_FAILOVER_CHAIN': ','.join(STRICT_CHAIN),
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
    state['policy'] = 'strict-residential-v1'
    state['outbounds'] = {
        f'CLIENT_{outbound.rsplit("-", 1)[-1].upper()}': outbound
        for outbound in STRICT_CHAIN
    }
    state['client_assignments'] = assignments
    write_json(STATE, state)


def assert_config(config, assignments):
    rules = config.get('routing', {}).get('rules', [])
    forbidden = {'residential-r1', 'residential-r2', 'fallback-la', 'fallback-bwh'}
    for rule in rules:
        outbound = rule.get('outboundTag')
        if outbound in forbidden:
            raise RuntimeError(f'forbidden route remains: {outbound}')
        if outbound == 'direct' and set(rule.get('domain') or []) - DIRECT_DOMAINS:
            raise RuntimeError('direct rule exceeds API allowlist')
    for outbound in STRICT_CHAIN:
        expected = {member for member, target in assignments.items() if target == outbound}
        matches = [
            rule for rule in rules
            if set(rule.get('user') or []) == expected
            and not rule.get('domain')
            and rule.get('outboundTag') == outbound
        ]
        if len(matches) != 1:
            raise RuntimeError(f'route rule mismatch for {outbound}: {len(matches)}')
    if not rules or rules[-1].get('outboundTag') != STRICT_CHAIN[0]:
        raise RuntimeError('unlisted user default is not residential-r3')
    payment_domains = load_payment_domains(PAYMENT_DOMAINS)
    for rule in rules:
        if PAYMENT_EXEMPTIONS.intersection(rule.get('domain') or []):
            raise RuntimeError('Alipay/WeChat exemption leaked into server rules')
        if set(rule.get('domain') or []).intersection(payment_domains) and rule.get('outboundTag') != 'residential-r3':
            raise RuntimeError('payment route is not residential-only')


def restart_and_verify(assignments):
    run(['x-ui', 'restart'], timeout=45)
    time.sleep(4)
    run([str(XRAY), 'run', '-test', '-c', str(CONFIG)], timeout=30)
    if not run(['pgrep', '-f', 'xray-linux-amd64'], check=False).stdout.strip():
        raise RuntimeError('xray process missing after restart')
    assert_config(read_json(CONFIG), assignments)


def restore(manifest):
    wait_for_failover_idle()
    for key, destination in {
        'config': CONFIG,
        'db': DB,
        'env': ENV,
        'state': STATE,
        'groups': GROUPS,
        'policy': POLICY,
        'payment_domains': PAYMENT_DOMAINS,
        'failover': FAILOVER,
        'group_routing': GROUP_ROUTING,
    }.items():
        source = manifest.get(key)
        if source:
            shutil.copy2(source, destination)
    restart_and_verify(manifest['assignments'])
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

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    wait_for_failover_idle()
    manifest = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'node': NODE,
        'assignments': assignments,
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
        run([sys.executable, str(GROUP_ROUTING)], env=os.environ.copy(), timeout=90)
        update_state(assignments)
        restart_and_verify(assignments)
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
