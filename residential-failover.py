#!/usr/bin/env python3
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, cast

CONFIG = '/usr/local/x-ui/bin/config.json'
DB = '/etc/x-ui/x-ui.db'
XRAY = '/usr/local/x-ui/bin/xray-linux-amd64'
STATE_FILE = '/etc/x-ui/residential-failover-state.json'
LOG_FILE = '/var/log/residential-failover.log'
POLICY_FILE = os.environ.get(
    'CLIENT_RESIDENTIAL_POLICY_FILE',
    '/etc/x-ui/client-residential-assignments.json',
)
PAYMENT_DOMAINS_FILE = os.environ.get('PAYMENT_DOMAINS_FILE', '/etc/x-ui/payment-domains.json')
SOCKS_ENV = os.environ.get('RESIDENTIAL_SOCKS_ENV', '/etc/x-ui/residential-socks.env')
FAIL_THRESHOLD = 3
RECOVERY_THRESHOLD = 5
PROBE_TIMEOUT = 8
PROBE_URL = 'https://api.ipify.org'
DRY_RUN = '--dry-run' in sys.argv

RESIDENTIAL_TAGS = (
    'residential-r1', 'residential-r2', 'residential-r3',
    'residential-r4', 'residential-r5', 'residential-r6',
)
SAFE_TAGS = ('residential-r3', 'residential-r4', 'residential-r5', 'residential-r6')
R1_R2_TAGS = ('residential-r1', 'residential-r2')
GITHUB_API_DOMAINS = ['domain:api.github.com']
GITHUB_API_CHAIN = ('direct', 'residential-r2')
GITHUB_PROBE_KEYS = ('github-direct', 'github-r2')
GITHUB_PROBE_INTERVAL = 300
GITHUB_API_PROBE_URL = os.environ.get(
    'GITHUB_API_PROBE_URL',
    'https://api.github.com/repos/XucroYuri/net-rules/git/trees/main?recursive=1',
)
R1_PUBLIC_IP = os.environ.get('R1_PUBLIC_IP', '192.204.62.110')
R2_PUBLIC_IP = os.environ.get('R2_PUBLIC_IP', '38.150.34.205')

GOOGLE_WEB_DOMAINS = [
    'domain:google.com', 'domain:gstatic.com', 'domain:googleusercontent.com',
    'domain:ai.google.dev', 'domain:google.dev', 'domain:youtube.com',
    'domain:youtu.be', 'domain:ytimg.com', 'domain:googlevideo.com',
    'domain:ggpht.com', 'domain:gemini.google.com', 'domain:bard.google.com',
    'domain:aistudio.google.com', 'domain:makersuite.google.com',
    'domain:alkalimakersuite-pa.clients6.google.com', 'domain:deepmind.google',
    'domain:labs.google', 'domain:notebooklm.google', 'domain:workspace.google.com',
    'domain:about.google', 'domain:blog.google', 'domain:withgoogle.com',
    'domain:googleblog.com', 'domain:google.org', 'keyword:gemini',
]


def load_env_file(path=SOCKS_ENV):
    if not os.path.exists(path):
        return
    with open(path) as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_payment_domains():
    with open(PAYMENT_DOMAINS_FILE) as handle:
        domains = json.load(handle)
    if not isinstance(domains, list) or len(domains) != 50:
        raise RuntimeError('payment domain list must contain 50 entries')
    return domains


def load_policy():
    with open(POLICY_FILE) as handle:
        policy = json.load(handle)
    order = tuple(policy.get('failover_order') or ())
    groups = cast(dict[str, Any], policy.get('groups') or {})
    if order != RESIDENTIAL_TAGS or set(groups) != set(RESIDENTIAL_TAGS):
        raise RuntimeError('six-group policy is incomplete')
    members_by_group: dict[str, list[str]] = {}
    assigned: dict[str, str] = {}
    chains: dict[str, list[str]] = {}
    for outbound in RESIDENTIAL_TAGS:
        members = list(groups[outbound].get('members') or [])
        chain_value = groups[outbound].get('failover_chain')
        if not members or len(members) != len(set(members)):
            raise RuntimeError(f'invalid members for {outbound}')
        if not isinstance(chain_value, list) or not chain_value or any(
            tag not in RESIDENTIAL_TAGS for tag in chain_value
        ):
            raise RuntimeError(f'invalid failover chain for {outbound}')
        chain = cast(list[str], chain_value)
        members_by_group[outbound] = members
        chains[outbound] = list(chain)
        for member in members:
            if member in assigned:
                raise RuntimeError(f'duplicate client assignment: {member}')
            assigned[member] = outbound
    additional = cast(dict[str, str], policy.get('additional_assignments') or {})
    for member, outbound in additional.items():
        if outbound not in RESIDENTIAL_TAGS or member in assigned:
            raise RuntimeError(f'invalid additional assignment: {member}')
        members_by_group[outbound].append(member)
        assigned[member] = outbound
    google_chain = tuple(cast(list[str], policy.get('google_r1_r2_failover_chain') or []))
    payment_chain = tuple(cast(list[str], policy.get('payment_failover_chain') or []))
    if google_chain != SAFE_TAGS or payment_chain != SAFE_TAGS:
        raise RuntimeError('Google/payment failover must be R3/R4/R5/R6 only')
    github_domains = list(policy.get('github_api_domains') or [])
    github_chain = tuple(cast(list[str], policy.get('github_api_failover_chain') or []))
    if github_domains != GITHUB_API_DOMAINS or github_chain != GITHUB_API_CHAIN:
        raise RuntimeError('GitHub API failover must be direct VPS -> residential-r2')
    overrides = cast(dict[str, dict[str, Any]], policy.get('openai_user_overrides') or {})
    for user, override in overrides.items():
        if user not in assigned:
            raise RuntimeError(f'OpenAI override user is not assigned: {user}')
        domains = override.get('domains')
        outbound = override.get('outbound')
        chain_value = override.get('failover_chain')
        if not isinstance(chain_value, list):
            raise RuntimeError(f'invalid OpenAI override chain: {user}')
        chain = tuple(cast(list[str], chain_value))
        if (not isinstance(domains, list) or not domains
                or any(not isinstance(domain, str) for domain in domains)):
            raise RuntimeError(f'invalid OpenAI override domains: {user}')
        if outbound not in SAFE_TAGS or not chain or not set(chain).issubset(SAFE_TAGS):
            raise RuntimeError(f'OpenAI override must use R3/R4/R5/R6: {user}')
        if chain[0] != outbound:
            raise RuntimeError(f'OpenAI override chain must start with outbound: {user}')
    return (
        policy, members_by_group, assigned, chains,
        list(google_chain), list(payment_chain), overrides, list(github_chain),
    )


load_env_file()
(
    POLICY, GROUP_MEMBERS, ASSIGNED_USERS, GROUP_CHAINS,
    GOOGLE_CHAIN, PAYMENT_CHAIN, OPENAI_USER_OVERRIDES, GITHUB_CHAIN,
) = load_policy()
PAYMENT_DOMAINS = load_payment_domains()
NODE = os.environ.get('NODE_ROLE', 'unknown').upper()
WG = {
    'r1': {'iface': 'wg0', 'expected_ip': R1_PUBLIC_IP},
    'r2': {'iface': 'wg1', 'expected_ip': R2_PUBLIC_IP},
}
RESOURCE_KEYS = ('r1', 'r2', 'r3', 'r4', 'r5', 'r6', *GITHUB_PROBE_KEYS)


def log(message):
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    line = f'[{stamp}] {message}'
    print(line)
    try:
        with open(LOG_FILE, 'a') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


def probe_wireguard(key):
    setting = WG[key]
    try:
        result = subprocess.run([
            'curl', '-4', '-s', '--max-time', str(PROBE_TIMEOUT),
            '--interface', setting['iface'], PROBE_URL,
        ], capture_output=True, text=True, timeout=PROBE_TIMEOUT + 2)
        actual = result.stdout.strip()
        return actual == setting['expected_ip'], f'{key} via {setting["iface"]} -> {actual or "empty"}'
    except Exception as error:
        return False, f'{key} via {setting["iface"]} -> {str(error)[:80]}'


def probe_socks(key):
    prefix = 'LYCHEE_' + key.upper()
    host = os.environ.get(prefix + '_HOST') or os.environ.get(prefix + '_PUBLIC_IP')
    expected = os.environ.get(prefix + '_PUBLIC_IP') or host
    port = os.environ.get(prefix + '_PORT')
    user = os.environ.get(prefix + '_USERNAME')
    password = os.environ.get(prefix + '_PASSWORD')
    if not all((host, expected, port, user, password)):
        return False, f'{key} SOCKS5 -> credentials missing'
    try:
        result = subprocess.run([
            'curl', '-4', '--fail', '--silent', '--show-error',
            '--max-time', str(PROBE_TIMEOUT), '--socks5-hostname', f'{host}:{port}',
            '--proxy-user', f'{user}:{password}', PROBE_URL,
        ], capture_output=True, text=True, timeout=PROBE_TIMEOUT + 2)
        actual = result.stdout.strip()
        return result.returncode == 0 and actual == expected, f'{key} SOCKS5 -> {actual or "empty"}'
    except Exception as error:
        return False, f'{key} SOCKS5 -> {str(error)[:80]}'


def probe_github_api(key):
    args = [
        'curl', '-4', '--fail', '--silent', '--show-error',
        '--max-time', str(PROBE_TIMEOUT),
        '-A', 'VPN-server-github-health/1.0',
        '-o', '/dev/null', '-w', '%{http_code}', GITHUB_API_PROBE_URL,
    ]
    label = 'direct' if key == 'github-direct' else 'r2'
    if key == 'github-r2':
        args[3:3] = ['--interface', 'wg1']
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=PROBE_TIMEOUT + 2,
        )
        code = result.stdout.strip()
        return result.returncode == 0 and code == '200', f'{label} GitHub tree -> HTTP {code or "empty"}'
    except Exception as error:
        return False, f'{label} GitHub tree -> {str(error)[:80]}'


def probe(key):
    if key in GITHUB_PROBE_KEYS:
        return probe_github_api(key)
    return probe_wireguard(key) if key in WG else probe_socks(key)


def group_descriptor(name: str, members: list[str], candidates: list[str], domains=None) -> dict[str, Any]:
    return {
        'name': name,
        'members': list(members),
        'domains': list(domains or []),
        'candidates': list(candidates),
    }


def build_groups() -> dict[str, dict[str, Any]]:
    groups = {
        f'CLIENT_{outbound.rsplit("-", 1)[-1].upper()}': group_descriptor(
            f'CLIENT_{outbound.rsplit("-", 1)[-1].upper()}',
            GROUP_MEMBERS[outbound],
            GROUP_CHAINS[outbound],
        )
        for outbound in RESIDENTIAL_TAGS
    }
    groups['GOOGLE_R1_R2'] = group_descriptor(
        'GOOGLE_R1_R2',
        GROUP_MEMBERS['residential-r1'] + GROUP_MEMBERS['residential-r2'],
        GOOGLE_CHAIN,
        GOOGLE_WEB_DOMAINS,
    )
    for user, override in OPENAI_USER_OVERRIDES.items():
        groups['OPENAI_' + user.upper().replace('-', '_')] = group_descriptor(
            'OPENAI_' + user.upper().replace('-', '_'),
            [user],
            list(override['failover_chain']),
            override['domains'],
        )
    groups['PAYMENT'] = group_descriptor('PAYMENT', [], PAYMENT_CHAIN, PAYMENT_DOMAINS)
    groups['GITHUB_API'] = group_descriptor('GITHUB_API', [], GITHUB_CHAIN, GITHUB_API_DOMAINS)
    return groups


GROUPS = build_groups()


def load_state() -> dict[str, Any]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as handle:
            return json.load(handle)
    return {
        '_updated': datetime.now(timezone.utc).isoformat(),
        'outbounds': {name: data['candidates'][0] for name, data in GROUPS.items()},
        'switch_history': [],
    }


def ensure_state_shape(state: dict[str, Any]) -> dict[str, Any]:
    for key in RESOURCE_KEYS:
        state.setdefault(key, {'fail_count': 0, 'success_count': 0, 'healthy': True})
    state.setdefault('outbounds', {})
    state.setdefault('switch_history', [])
    return state


def candidate_state_key(candidate, group_name=None):
    if group_name == 'GITHUB_API':
        return 'github-direct' if candidate == 'direct' else 'github-r2'
    return candidate.rsplit('-', 1)[-1]


def find_rule(rules, descriptor):
    expected_users = set(descriptor['members'])
    expected_domains = set(descriptor['domains'])
    for index, rule in enumerate(rules):
        actual_users = set(rule.get('user') or [])
        actual_domains = set(rule.get('domain') or [])
        if actual_users == expected_users and actual_domains == expected_domains:
            return index, rule
    return None, None


def current_outbound(cfg, descriptor):
    _, rule = find_rule(cfg.get('routing', {}).get('rules', []), descriptor)
    return rule.get('outboundTag') if rule else None


def change_outbound(cfg, descriptor, new_outbound):
    rules = cfg.get('routing', {}).get('rules', [])
    index, rule = find_rule(rules, descriptor)
    if rule is None:
        log(f'WARN: {descriptor["name"]} rule not found')
        return False
    old = rule.get('outboundTag')
    if old == new_outbound:
        return False
    rule['outboundTag'] = new_outbound
    log(f'rule[{index}] {descriptor["name"]}: {old} -> {new_outbound}')
    return True


def do_switches(switches):
    for descriptor, old, new, reason in switches:
        log(f'SWITCH: {descriptor["name"]} {old}->{new} ({reason})')
    if DRY_RUN:
        return True
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    db_backup = f'{DB}.bak-fo-v2-{stamp}'
    config_backup = f'{CONFIG}.bak-fo-v2-{stamp}'
    shutil.copy2(DB, db_backup)
    shutil.copy2(CONFIG, config_backup)
    connection = None
    preview = None
    try:
        with open(CONFIG) as handle:
            cfg = json.load(handle)
        outbound_tags = {item.get('tag') for item in cfg.get('outbounds', [])}
        for descriptor, _, new, _ in switches:
            if new not in outbound_tags:
                raise RuntimeError(f'outbound not found: {new}')
            if not change_outbound(cfg, descriptor, new):
                raise RuntimeError(f'rule not changed: {descriptor["name"]}')
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', prefix='xray-failover-v2-',
            dir=os.path.dirname(CONFIG), delete=False,
        ) as handle:
            json.dump(cfg, handle, indent=2)
            preview = handle.name
        result = subprocess.run([XRAY, 'run', '-test', '-c', preview], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout)[-1200:])
        connection = sqlite3.connect(DB)
        row = connection.execute(
            "SELECT value FROM settings WHERE key='xrayTemplateConfig'"
        ).fetchone()
        if not row or not row[0]:
            raise RuntimeError('xrayTemplateConfig not found')
        template = json.loads(row[0])
        for descriptor, _, new, _ in switches:
            if not change_outbound(template, descriptor, new):
                raise RuntimeError(f'template rule not changed: {descriptor["name"]}')
        connection.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(template, indent=2),),
        )
        connection.commit()
        connection.close()
        connection = None
        restart = subprocess.run(['x-ui', 'restart'], capture_output=True, text=True, timeout=45)
        if restart.returncode != 0:
            raise RuntimeError((restart.stderr or restart.stdout)[-1200:])
        time.sleep(3)
        if not subprocess.run(['pgrep', '-f', 'xray-linux-amd64'], capture_output=True, text=True).stdout.strip():
            raise RuntimeError('xray process missing after restart')
        with open(CONFIG) as handle:
            live = json.load(handle)
        for descriptor, _, new, _ in switches:
            if current_outbound(live, descriptor) != new:
                raise RuntimeError(f'generated config did not persist {descriptor["name"]}')
    except Exception as error:
        log(f'ERROR rollback: {error}')
        if connection is not None:
            connection.close()
        shutil.copy2(db_backup, DB)
        shutil.copy2(config_backup, CONFIG)
        subprocess.run(['x-ui', 'restart'], capture_output=True, timeout=45)
        return False
    finally:
        if preview and os.path.exists(preview):
            os.unlink(preview)
    log(f'DONE: {len(switches)} route categories switched')
    return True


def main():
    log(f'=== Check V2 (node={NODE} dry_run={DRY_RUN}) ===')
    state = ensure_state_shape(load_state())
    probe_results = {}
    due_keys = []
    now = time.time()
    for key in RESOURCE_KEYS:
        resource = cast(dict[str, Any], state[key])
        last_probe = float(resource.get('last_github_probe', 0) or 0)
        if key in GITHUB_PROBE_KEYS and now - last_probe < GITHUB_PROBE_INTERVAL:
            label = 'direct' if key == 'github-direct' else 'r2'
            probe_results[key] = (resource['healthy'], f'{label} GitHub tree -> skipped (interval)')
        else:
            due_keys.append(key)
    if due_keys:
        with ThreadPoolExecutor(max_workers=len(due_keys)) as executor:
            probe_results.update(dict(zip(due_keys, executor.map(probe, due_keys))))
    for key in RESOURCE_KEYS:
        healthy, detail = probe_results[key]
        log(detail)
        resource = cast(dict[str, Any], state[key])
        if key in GITHUB_PROBE_KEYS and key in due_keys:
            resource['last_github_probe'] = now
        resource['fail_count'] = 0 if healthy else resource['fail_count'] + 1
        resource['success_count'] = resource['success_count'] + 1 if healthy else 0
        was_healthy = resource['healthy']
        if resource['fail_count'] >= FAIL_THRESHOLD:
            resource['healthy'] = False
        if resource['success_count'] >= RECOVERY_THRESHOLD:
            resource['healthy'] = True
        if was_healthy != resource['healthy']:
            log(f'{key.upper()} {was_healthy}->{resource["healthy"]}')

    with open(CONFIG) as handle:
        cfg = json.load(handle)
    switches = []
    for name, descriptor in GROUPS.items():
        current = current_outbound(cfg, descriptor)
        if current:
            state['outbounds'][name] = current
        target = next((candidate for candidate in descriptor['candidates']
                       if state[candidate_state_key(candidate, name)]['healthy']), None)
        if not target:
            log(f'WARN: {name} has no healthy residential candidate; keeping {current}')
            continue
        if current == target:
            continue
        reason = 'preferred residential chain: ' + ' -> '.join(descriptor['candidates'])
        switches.append((descriptor, current, target, reason))

    if switches and do_switches(switches):
        for descriptor, current, target, reason in switches:
            state['outbounds'][descriptor['name']] = target
            state['switch_history'].append({
                'time': datetime.now(timezone.utc).isoformat(),
                'group': descriptor['name'], 'from': current, 'to': target, 'reason': reason,
            })
        state['switch_history'] = state['switch_history'][-30:]
    if not DRY_RUN:
        state['_updated'] = datetime.now(timezone.utc).isoformat()
        with open(STATE_FILE, 'w') as handle:
            json.dump(state, handle, indent=2)
    log('=== Done. ' + ' '.join(
        f'{name}={state["outbounds"].get(name, "?")}' for name in GROUPS
    ) + ' ===')


if __name__ == '__main__':
    main()
