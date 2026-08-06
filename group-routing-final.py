#!/usr/bin/env python3
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, cast

CONFIG = '/usr/local/x-ui/bin/config.json'
DB = '/etc/x-ui/x-ui.db'
XRAY = '/usr/local/x-ui/bin/xray-linux-amd64'
GROUPS_PATH = '/etc/x-ui/client-groups.json'
POLICY_PATH = os.environ.get(
    'CLIENT_RESIDENTIAL_POLICY_FILE',
    '/etc/x-ui/client-residential-assignments.json',
)
SOCKS_ENV = os.environ.get('RESIDENTIAL_SOCKS_ENV', '/etc/x-ui/residential-socks.env')
PAYMENT_DOMAINS_FILE = os.environ.get('PAYMENT_DOMAINS_FILE', '/etc/x-ui/payment-domains.json')
TS = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
DRY_RUN = '--dry-run' in sys.argv

RESIDENTIAL_TAGS = (
    'residential-r1', 'residential-r2', 'residential-r3',
    'residential-r4', 'residential-r5', 'residential-r6',
)
GOOGLE_SAFE_CHAIN = (
    'residential-r3', 'residential-r4', 'residential-r5', 'residential-r6',
)
OPENAI_SAFE_CHAIN = GOOGLE_SAFE_CHAIN

GOOGLE_CLOUD_API_EXCEPTIONS = [
    'domain:cloud.google.com', 'domain:firebase.google.com',
    'domain:googleapis.com', 'domain:googleapi.com', 'domain:googlecloud.com',
    'domain:cloud.google', 'domain:gcr.io', 'domain:pkg.dev',
    'domain:run.app', 'domain:appspot.com', 'domain:cloudfunctions.net',
    'domain:firebaseio.com', 'domain:firebaseapp.com',
]
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


def load_payment_domains(path):
    with open(path) as handle:
        domains = json.load(handle)
    if not isinstance(domains, list) or len(domains) != 50 or any(
        not isinstance(domain, str)
        or not domain.startswith(('domain:', 'keyword:', 'geosite:'))
        for domain in domains
    ):
        raise RuntimeError(f'invalid payment domain list: {path}')
    return domains


def load_assignment_policy(path):
    with open(path) as handle:
        policy = json.load(handle)
    groups = policy.get('groups')
    order = tuple(policy.get('failover_order') or ())
    if order != RESIDENTIAL_TAGS or not isinstance(groups, dict) or set(groups) != set(order):
        raise RuntimeError(f'invalid six-group residential policy: {path}')

    members_by_group = {}
    assigned = {}
    for outbound in order:
        group = groups[outbound]
        members = group.get('members')
        chain_value = group.get('failover_chain')
        if not isinstance(members, list) or not members or any(not isinstance(item, str) for item in members):
            raise RuntimeError(f'invalid members for {outbound}')
        if len(members) != len(set(members)):
            raise RuntimeError(f'duplicate member in {outbound}')
        if not isinstance(chain_value, list) or not chain_value or any(
            tag not in RESIDENTIAL_TAGS for tag in chain_value
        ):
            raise RuntimeError(f'invalid failover chain for {outbound}')
        members_by_group[outbound] = list(members)
        for member in members:
            if member in assigned:
                raise RuntimeError(f'{member} appears in multiple residential groups')
            assigned[member] = outbound

    for member, outbound in (policy.get('additional_assignments') or {}).items():
        if outbound not in RESIDENTIAL_TAGS or member in assigned:
            raise RuntimeError(f'invalid additional assignment: {member} -> {outbound}')
        members_by_group[outbound].append(member)
        assigned[member] = outbound

    for key in ('google_r1_r2_failover_chain', 'payment_failover_chain'):
        chain = tuple(policy.get(key) or ())
        if chain != GOOGLE_SAFE_CHAIN:
            raise RuntimeError(f'{key} must be R3/R4/R5/R6 only')
    overrides = policy.get('openai_user_overrides') or {}
    if not isinstance(overrides, dict):
        raise RuntimeError('openai_user_overrides must be an object')
    for user, override in overrides.items():
        if user not in assigned or not isinstance(override, dict):
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
        if outbound not in OPENAI_SAFE_CHAIN or not chain or not set(chain).issubset(OPENAI_SAFE_CHAIN):
            raise RuntimeError(f'OpenAI override for {user} must use R3/R4/R5/R6')
        if chain[0] != outbound:
            raise RuntimeError(f'OpenAI override chain must start with outbound for {user}')
    return policy, members_by_group, assigned, overrides


load_env_file()
PAYMENT_DOMAINS = load_payment_domains(PAYMENT_DOMAINS_FILE)
ASSIGNMENT_POLICY, GROUP_MEMBERS, ASSIGNED_USERS, OPENAI_USER_OVERRIDES = load_assignment_policy(POLICY_PATH)
NODE = os.environ.get('NODE_ROLE', 'LA').upper()
DEFAULT_RESIDENTIAL_OUTBOUND = 'residential-r3'
PAYMENT_EXEMPTIONS = ASSIGNMENT_POLICY.get('client_direct_domains') or []

SENDTHROUGH_MAP = {
    'LA': {'residential-r1': '10.10.10.2', 'residential-r2': '10.10.11.2'},
    'BWH': {'residential-r1': '10.10.12.2', 'residential-r2': '10.10.13.2'},
}
FALLBACK_TAGS = ('fallback-la', 'fallback-bwh')
FALLBACK_SENDTHROUGH_MAP = {
    'LA': {'fallback-la': None, 'fallback-bwh': '10.10.18.1'},
    'BWH': {'fallback-la': '10.10.18.2', 'fallback-bwh': None},
}


def env_prefix_for_outbound(outbound):
    return 'LYCHEE_' + outbound.rsplit('-', 1)[-1].upper()


def socks_outbound(outbound):
    prefix = env_prefix_for_outbound(outbound)
    host = os.environ.get(prefix + '_HOST') or os.environ.get(prefix + '_PUBLIC_IP')
    port = os.environ.get(prefix + '_PORT')
    user = os.environ.get(prefix + '_USERNAME')
    password = os.environ.get(prefix + '_PASSWORD')
    if os.environ.get(prefix + '_PROTOCOL', 'socks5') != 'socks5' or not all((host, port, user, password)):
        return None
    return {
        'tag': outbound,
        'protocol': 'socks',
        'settings': {
            'servers': [{
                'address': host,
                'port': int(port or '0'),
                'users': [{'user': user, 'pass': password}],
            }],
        },
    }


def build_rules():
    r1_r2_users = GROUP_MEMBERS['residential-r1'] + GROUP_MEMBERS['residential-r2']
    rules = [
        {'inboundTag': ['api'], 'outboundTag': 'api', 'type': 'field'},
        {'type': 'field', 'port': '443', 'network': 'udp', 'outboundTag': 'blocked'},
        {'type': 'field', 'domain': [
            'domain:battle.net', 'domain:blizzard.com', 'domain:gog.com',
            'domain:gog-cdn.com', 'domain:riotgames.com', 'domain:leagueoflegends.com',
        ], 'outboundTag': 'blocked'},
        {'type': 'field', 'domain': [
            'domain:google-analytics.com', 'domain:googletagmanager.com',
            'domain:googletagservices.com', 'domain:doubleclick.net',
            'domain:googleadservices.com', 'domain:connect.facebook.net',
            'domain:facebook.net', 'domain:snap.licdn.com', 'domain:ads-twitter.com',
            'domain:hotjar.com', 'domain:crazyegg.com', 'domain:fullstory.com',
            'domain:scorecardresearch.com', 'domain:quantserve.com', 'domain:adnxs.com',
            'domain:criteo.com', 'domain:taboola.com', 'domain:outbrain.com',
            'domain:segment.io', 'domain:mixpanel.com', 'domain:amplitude.com',
        ], 'outboundTag': 'blocked'},
        {'type': 'field', 'domain': GOOGLE_CLOUD_API_EXCEPTIONS, 'outboundTag': 'direct'},
        {'type': 'field', 'domain': PAYMENT_EXEMPTIONS, 'outboundTag': 'direct'},
        {'ip': ['geoip:private'], 'outboundTag': 'blocked', 'type': 'field'},
        {'outboundTag': 'blocked', 'protocol': ['bittorrent'], 'type': 'field'},
        {'type': 'field', 'domain': PAYMENT_DOMAINS, 'outboundTag': 'residential-r3'},
        {
            'type': 'field',
            'user': r1_r2_users,
            'domain': GOOGLE_WEB_DOMAINS,
            'outboundTag': 'residential-r3',
        },
    ]
    for user, override in OPENAI_USER_OVERRIDES.items():
        rules.append({
            'type': 'field',
            'user': [user],
            'domain': override['domains'],
            'outboundTag': override['outbound'],
        })
    for outbound in RESIDENTIAL_TAGS:
        rules.append({
            'type': 'field',
            'user': GROUP_MEMBERS[outbound],
            'outboundTag': outbound,
        })
    rules.append({'type': 'field', 'network': 'tcp,udp', 'outboundTag': DEFAULT_RESIDENTIAL_OUTBOUND})
    return rules


def apply_routing(cfg, rules):
    if NODE not in FALLBACK_SENDTHROUGH_MAP:
        raise RuntimeError(f'unsupported NODE_ROLE: {NODE}')
    obs = cast(list[dict[str, Any]], cfg.get('outbounds', []))
    node_st = SENDTHROUGH_MAP[NODE]
    for outbound_tag in ('residential-r1', 'residential-r2'):
        existing = next((item for item in obs if item.get('tag') == outbound_tag), None)
        if existing is None:
            existing = {'tag': outbound_tag}
            obs.append(existing)
        outbound = cast(dict[str, Any], existing)
        outbound['protocol'] = 'freedom'
        outbound['settings'] = {'domainStrategy': 'AsIs'}
        outbound['sendThrough'] = node_st[outbound_tag]

    tags = {item.get('tag') for item in obs}
    for outbound_tag in RESIDENTIAL_TAGS[2:]:
        if outbound_tag in tags:
            continue
        outbound = socks_outbound(outbound_tag)
        if outbound is None:
            raise RuntimeError(f'missing SOCKS5 credentials for {outbound_tag} in {SOCKS_ENV}')
        obs.append(outbound)
        tags.add(outbound_tag)

    fallback_sendthrough = FALLBACK_SENDTHROUGH_MAP[NODE]
    for tag in FALLBACK_TAGS:
        existing = next((item for item in obs if item.get('tag') == tag), None)
        if existing is None:
            existing = {'tag': tag}
            obs.append(existing)
        outbound = cast(dict[str, Any], existing)
        outbound['protocol'] = 'freedom'
        outbound['settings'] = {'domainStrategy': 'UseIPv4'}
        send_through = fallback_sendthrough[tag]
        if send_through:
            outbound['sendThrough'] = send_through
        else:
            outbound.pop('sendThrough', None)

    direct = next((item for item in obs if item.get('tag') == 'direct'), None)
    if direct is None:
        direct = {'tag': 'direct', 'protocol': 'freedom', 'settings': {}}
        obs.insert(0, direct)
    direct = cast(dict[str, Any], direct)
    direct.setdefault('settings', {})['domainStrategy'] = 'UseIPv4'
    if 'blocked' not in {item.get('tag') for item in obs}:
        obs.append({'tag': 'blocked', 'protocol': 'blackhole', 'settings': {}})
    cfg['outbounds'] = obs
    cfg['routing'] = {'domainStrategy': 'IPIfNonMatch', 'rules': rules}
    return cfg


def active_users(cfg):
    return {
        client['email']
        for inbound in cfg.get('inbounds', [])
        for client in (inbound.get('settings') or {}).get('clients') or []
        if client.get('email')
    }


def validate_config(cfg):
    rules = cfg.get('routing', {}).get('rules', [])
    allowed_direct = set(GOOGLE_CLOUD_API_EXCEPTIONS + PAYMENT_EXEMPTIONS)
    for rule in rules:
        outbound = rule.get('outboundTag')
        if outbound in FALLBACK_TAGS:
            raise RuntimeError(f'VPS fallback route is forbidden in client rules: {outbound}')
        if outbound == 'direct' and set(rule.get('domain') or []) - allowed_direct:
            raise RuntimeError('direct rule contains a domain outside API/payment exceptions')

    for outbound in RESIDENTIAL_TAGS:
        matches = [
            rule for rule in rules
            if set(rule.get('user') or []) == set(GROUP_MEMBERS[outbound])
            and not rule.get('domain') and rule.get('outboundTag') == outbound
        ]
        if len(matches) != 1:
            raise RuntimeError(f'expected one group rule for {outbound}, found {len(matches)}')

    payment_matches = [
        rule for rule in rules
        if not rule.get('user') and set(rule.get('domain') or []) == set(PAYMENT_DOMAINS)
        and rule.get('outboundTag') == 'residential-r3'
    ]
    if len(payment_matches) != 1:
        raise RuntimeError(f'expected one payment rule, found {len(payment_matches)}')

    google_users = set(GROUP_MEMBERS['residential-r1'] + GROUP_MEMBERS['residential-r2'])
    google_matches = [
        rule for rule in rules
        if set(rule.get('user') or []) == google_users
        and set(rule.get('domain') or []) == set(GOOGLE_WEB_DOMAINS)
        and rule.get('outboundTag') == 'residential-r3'
    ]
    if len(google_matches) != 1:
        raise RuntimeError(f'expected one R1/R2 Google rule, found {len(google_matches)}')
    for user, override in OPENAI_USER_OVERRIDES.items():
        matches = [
            rule for rule in rules
            if rule.get('user') == [user]
            and set(rule.get('domain') or []) == set(override['domains'])
            and rule.get('outboundTag') == override['outbound']
        ]
        if len(matches) != 1:
            raise RuntimeError(f'expected one OpenAI override for {user}, found {len(matches)}')
    if not rules or rules[-1].get('outboundTag') != DEFAULT_RESIDENTIAL_OUTBOUND:
        raise RuntimeError('unlisted users do not have a residential default')

    unknown = active_users(cfg) - set(ASSIGNED_USERS)
    if unknown:
        print('unlisted_users=' + ','.join(sorted(unknown)))


def write_groups_file():
    r1_r2_users = GROUP_MEMBERS['residential-r1'] + GROUP_MEMBERS['residential-r2']
    with open(GROUPS_PATH, 'w') as handle:
        json.dump({
            '_updated': TS,
            '_arch': 'STRICT RESIDENTIAL V2: six groups, payment, R1/R2 Google and liulishuo OpenAI on R3-R6',
            'GOOGLE_CLOUD_API_EXCEPTIONS': {
                'domains': GOOGLE_CLOUD_API_EXCEPTIONS, 'outbound': 'direct',
            },
            'GOOGLE_R1_R2': {
                'domains': GOOGLE_WEB_DOMAINS,
                'members': r1_r2_users,
                'outbound': 'residential-r3',
                'failover_chain': list(GOOGLE_SAFE_CHAIN),
            },
            'OPENAI_USER_OVERRIDES': {
                user: {
                    'domains': override['domains'],
                    'outbound': override['outbound'],
                    'failover_chain': override['failover_chain'],
                }
                for user, override in OPENAI_USER_OVERRIDES.items()
            },
            'CLIENT_RESIDENTIAL': {
                outbound: {
                    'members': GROUP_MEMBERS[outbound],
                    'outbound': outbound,
                    'failover_chain': ASSIGNMENT_POLICY['groups'][outbound]['failover_chain'],
                }
                for outbound in RESIDENTIAL_TAGS
            },
            'PAYMENT': {
                'domains': PAYMENT_DOMAINS,
                'excluded_client_direct_domains': PAYMENT_EXEMPTIONS,
                'outbound': 'residential-r3',
                'failover_chain': list(GOOGLE_SAFE_CHAIN),
            },
            'DEFAULT_UNASSIGNED': {
                'outbound': DEFAULT_RESIDENTIAL_OUTBOUND,
                'failover_chain': list(GOOGLE_SAFE_CHAIN),
            },
        }, handle, indent=2)


def main():
    if ASSIGNMENT_POLICY.get('server_direct_domains') != GOOGLE_CLOUD_API_EXCEPTIONS:
        raise RuntimeError('server direct domains do not match the explicit API allowlist')
    print(f'=== Strict residential routing V2 (NODE={NODE}, groups={len(RESIDENTIAL_TAGS)}) ===')
    with open(CONFIG) as handle:
        cfg = json.load(handle)
    rules = build_rules()
    cfg = apply_routing(cfg, rules)
    validate_config(cfg)

    if DRY_RUN:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=os.path.dirname(CONFIG), delete=False) as handle:
            json.dump(cfg, handle, indent=2)
            preview = handle.name
        try:
            result = subprocess.run([XRAY, 'run', '-test', '-c', preview], capture_output=True, text=True)
        finally:
            os.unlink(preview)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout)[-1600:])
        print('active_users=' + ','.join(sorted(active_users(cfg))))
        print('group_rules=' + ','.join(f'{tag}:{len(GROUP_MEMBERS[tag])}' for tag in RESIDENTIAL_TAGS))
        print('payment_rule=R3->R4->R5->R6 google_r1_r2_rule=R3->R4->R5->R6')
        print('openai_user_overrides=' + ','.join(sorted(OPENAI_USER_OVERRIDES)))
        print('=== DRY RUN ===')
        return

    backup = f'{CONFIG}.bak-final-v2-{TS}'
    shutil.copy2(CONFIG, backup)
    connection = sqlite3.connect(DB)
    template_row = connection.execute(
        "SELECT value FROM settings WHERE key='xrayTemplateConfig'"
    ).fetchone()
    try:
        with open(CONFIG, 'w') as handle:
            json.dump(cfg, handle, indent=2)
        result = subprocess.run([XRAY, 'run', '-test', '-c', CONFIG], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout)[-1600:])
        if template_row and template_row[0]:
            template = json.loads(template_row[0])
            apply_routing(template, rules)
            connection.execute(
                "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
                (json.dumps(template, indent=2),),
            )
            connection.commit()
    except Exception:
        connection.close()
        shutil.copy2(backup, CONFIG)
        raise
    connection.close()
    write_groups_file()
    restart = subprocess.run(['x-ui', 'restart'], capture_output=True, text=True, timeout=45)
    if restart.returncode != 0:
        raise RuntimeError(restart.stderr[-1600:])
    time.sleep(3)
    if not subprocess.run(['pgrep', '-f', 'xray-linux-amd64'], capture_output=True, text=True).stdout.strip():
        raise RuntimeError('xray process missing after restart')
    print(f'[DONE] backup={backup} active_users={len(active_users(cfg))}')


if __name__ == '__main__':
    main()
