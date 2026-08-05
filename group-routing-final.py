#!/usr/bin/env python3
"""
Strict per-client residential routing for LA and BWH.

Every mapped client receives one catch-all residential rule. Only the explicit
Google Cloud/API allowlist is direct on the VPS. R1/R2 and VPS public fallbacks
remain provisioned for infrastructure recovery, but are never referenced by a
client rule in this policy.

Usage:
  NODE_ROLE=LA python3 group-routing-final.py [--dry-run]
  NODE_ROLE=BWH python3 group-routing-final.py [--dry-run]
"""
import json, sqlite3, shutil, subprocess, sys, os, time, re, tempfile
from datetime import datetime, timezone

CONFIG = '/usr/local/x-ui/bin/config.json'
DB = '/etc/x-ui/x-ui.db'
XRAY = '/usr/local/x-ui/bin/xray-linux-amd64'
GROUPS_PATH = '/etc/x-ui/client-groups.json'
POLICY_PATH = os.environ.get(
    'CLIENT_RESIDENTIAL_POLICY_FILE',
    '/etc/x-ui/client-residential-assignments.json',
)
SOCKS_ENV = os.environ.get('RESIDENTIAL_SOCKS_ENV', '/etc/x-ui/residential-socks.env')
ts = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
dry_run = '--dry-run' in sys.argv


def load_env_file(path=SOCKS_ENV):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env_file()

PAYMENT_DOMAINS_FILE = os.environ.get('PAYMENT_DOMAINS_FILE', '/etc/x-ui/payment-domains.json')


def load_payment_domains(path):
    with open(path) as handle:
        domains = json.load(handle)
    if not isinstance(domains, list) or not domains or any(
        not isinstance(domain, str) or not domain.startswith(('domain:', 'keyword:', 'geosite:'))
        for domain in domains
    ):
        raise RuntimeError(f'invalid payment domain list: {path}')
    return domains


PAYMENT_DOMAINS = load_payment_domains(PAYMENT_DOMAINS_FILE)

def load_assignment_policy(path):
    with open(path) as handle:
        policy = json.load(handle)
    groups = policy.get('groups')
    order = tuple(policy.get('failover_order') or ())
    if not isinstance(groups, dict) or set(groups) != set(order):
        raise RuntimeError(f'invalid residential assignment groups: {path}')
    if order != ('residential-r3', 'residential-r4', 'residential-r5'):
        raise RuntimeError(f'strict policy must be R3/R4/R5 only: {order}')

    members_by_group = {}
    assigned = {}
    for outbound in order:
        members = groups[outbound].get('members')
        if not isinstance(members, list) or not members or any(not isinstance(item, str) for item in members):
            raise RuntimeError(f'invalid members for {outbound}')
        if len(members) != len(set(members)):
            raise RuntimeError(f'duplicate member in {outbound}')
        members_by_group[outbound] = list(members)
        for member in members:
            if member in assigned:
                raise RuntimeError(f'{member} appears in multiple residential groups')
            assigned[member] = outbound

    additional = policy.get('additional_assignments', {})
    if not isinstance(additional, dict):
        raise RuntimeError('additional_assignments must be an object')
    for member, outbound in additional.items():
        if outbound not in order or member in assigned:
            raise RuntimeError(f'invalid additional assignment: {member} -> {outbound}')
        members_by_group[outbound].append(member)
        assigned[member] = outbound

    return policy, order, members_by_group, assigned


ASSIGNMENT_POLICY, STRICT_RESIDENTIAL_CHAIN, GROUP_MEMBERS, ASSIGNED_USERS = load_assignment_policy(POLICY_PATH)
PRIMARY_RESIDENTIAL_OUTBOUND = 'residential-r3'
DEFAULT_RESIDENTIAL_OUTBOUND = PRIMARY_RESIDENTIAL_OUTBOUND

NODE = os.environ.get('NODE_ROLE', 'LA').upper()
RESIDENTIAL_CHAIN = tuple(
    tag.strip() for tag in os.environ.get(
        'RESIDENTIAL_FAILOVER_CHAIN',
        'residential-r3,residential-r4,residential-r5,residential-r2,residential-r1,fallback-la,fallback-bwh',
    ).split(',') if tag.strip()
)
PAYMENT_RESIDENTIAL_CHAIN = tuple(
    tag.strip() for tag in os.environ.get(
        'PAYMENT_RESIDENTIAL_CHAIN', 'residential-r3,residential-r4,residential-r5'
    ).split(',') if tag.strip()
)
PAYMENT_ALLOWED_CHAIN = {'residential-r3', 'residential-r4', 'residential-r5'}
if not PAYMENT_RESIDENTIAL_CHAIN or any(tag not in PAYMENT_ALLOWED_CHAIN for tag in PAYMENT_RESIDENTIAL_CHAIN):
    raise RuntimeError(f'unsupported PAYMENT_RESIDENTIAL_CHAIN: {PAYMENT_RESIDENTIAL_CHAIN}')
PAYMENT_PRIMARY_RESIDENTIAL_OUTBOUND = os.environ.get(
    'PAYMENT_PRIMARY_RESIDENTIAL_TAG', PAYMENT_RESIDENTIAL_CHAIN[0]
)
if PAYMENT_PRIMARY_RESIDENTIAL_OUTBOUND not in PAYMENT_RESIDENTIAL_CHAIN:
    raise RuntimeError(f'unsupported PAYMENT_PRIMARY_RESIDENTIAL_TAG: {PAYMENT_PRIMARY_RESIDENTIAL_OUTBOUND}')

if any(tag not in RESIDENTIAL_CHAIN for tag in STRICT_RESIDENTIAL_CHAIN):
    raise RuntimeError(f'strict residential chain is not provisioned: {STRICT_RESIDENTIAL_CHAIN}')

# WG sendThrough IPs by Node
SENDTHROUGH_MAP = {
    'LA': {'residential-r1': '10.10.10.2', 'residential-r2': '10.10.11.2'},
    'BWH': {'residential-r1': '10.10.12.2', 'residential-r2': '10.10.13.2'},
    'HK': {'residential-r1': '10.10.14.2', 'residential-r2': '10.10.15.2'},
}

FALLBACK_TAGS = ('fallback-la', 'fallback-bwh')
FALLBACK_SENDTHROUGH_MAP = {
    'LA': {'fallback-la': None, 'fallback-bwh': '10.10.18.1'},
    'BWH': {'fallback-la': '10.10.18.2', 'fallback-bwh': None},
}

# ===== HARDCODED DOMAIN LIST =====
GOOGLE_CLOUD_API_EXCEPTIONS = [
    'domain:cloud.google.com','domain:firebase.google.com',
    'domain:googleapis.com','domain:googleapi.com','domain:googlecloud.com',
    'domain:cloud.google','domain:gcr.io','domain:pkg.dev',
    'domain:run.app','domain:appspot.com','domain:cloudfunctions.net',
    'domain:firebaseio.com','domain:firebaseapp.com',
]

GOOGLE_DOMAINS = [
    'domain:google.com','domain:gstatic.com',
    'domain:googleusercontent.com','domain:ai.google.dev',
    'domain:google.dev','domain:youtube.com','domain:youtu.be',
    'domain:ytimg.com','domain:googlevideo.com','domain:ggpht.com',
    'domain:gemini.google.com','domain:bard.google.com',
    'domain:aistudio.google.com','domain:makersuite.google.com',
    'domain:alkalimakersuite-pa.clients6.google.com',
    'domain:deepmind.google','domain:labs.google','domain:notebooklm.google',
    'domain:workspace.google.com',
    'domain:about.google','domain:blog.google','domain:withgoogle.com',
    'domain:googleblog.com','domain:google.org',
    'keyword:gemini',
]
OPENAI_DOMAINS = [
    'domain:openai.com','domain:chatgpt.com','domain:oaistatic.com',
    'domain:oaiusercontent.com','domain:chat.openai.com',
    'domain:platform.openai.com','domain:api.openai.com','domain:sora.com',
    'geosite:openai','keyword:openai','keyword:chatgpt',
]
CREATIVE_DOMAINS = [
    'domain:adobe.com','domain:adobestatic.com','domain:behance.net',
    'domain:autodesk.com','domain:figma.com',
    'domain:discord.com','domain:discordapp.com','domain:discord.gg',
    'domain:midjourney.com','domain:stability.ai',
]
SOCIAL_DOMAINS = [
    'domain:facebook.com','domain:instagram.com','domain:whatsapp.com',
    'domain:twitter.com','domain:x.com','domain:twimg.com',
    'domain:reddit.com','domain:redd.it',
]
WORK_DOMAINS = [
    'domain:linkedin.com','domain:slack.com','domain:notion.so',
]
STREAMING_DOMAINS = [
    'domain:netflix.com','domain:disneyplus.com','domain:hbo.com',
    'domain:hulu.com','domain:primevideo.com',
]
SPLIT_DOMAINS = [
    'domain:anthropic.com','domain:claude.ai','domain:claudeusercontent.com',
    'domain:perplexity.ai','domain:pplx.ai','domain:character.ai',
    'domain:poe.com','domain:quora.com','domain:you.com',
    'domain:copilot.microsoft.com','domain:bing.com',
    'domain:huggingface.co','domain:hf.co','domain:replicate.com',
    'domain:together.ai','domain:groq.com','domain:mistral.ai',
    'domain:openrouter.ai','domain:civitai.com','domain:coze.com',
    'domain:chat.deepseek.com','domain:tongyi.alibabacloud.com',
    'domain:zhipuai.ai','domain:open.bigmodel.ai','domain:kimi.ai',
    'domain:api.moonshot.ai','domain:z.ai','domain:chat.z.ai','domain:grok.com',
    'domain:leonardo.ai','domain:ideogram.ai','domain:gamma.app',
    'domain:phind.com','domain:suno.com','domain:elevenlabs.io',
    'domain:gitlab.com','domain:lottiefiles.com',
    'domain:medium.com','domain:udemy.com',
    'domain:shopify.com','domain:cdn.shopify.com','domain:myshopify.com',
    'keyword:anthropic','keyword:claude','keyword:generativeai',
]


def env_prefix_for_outbound(outbound):
    return 'LYCHEE_' + outbound.rsplit('-', 1)[-1].upper()


def socks_outbound(outbound):
    prefix = env_prefix_for_outbound(outbound)
    host = os.environ.get(prefix + '_HOST') or os.environ.get(prefix + '_PUBLIC_IP')
    port = os.environ.get(prefix + '_PORT')
    user = os.environ.get(prefix + '_USERNAME')
    password = os.environ.get(prefix + '_PASSWORD')
    protocol = os.environ.get(prefix + '_PROTOCOL', 'socks5')
    if protocol != 'socks5' or not all([host, port, user, password]):
        return None
    return {
        'tag': outbound,
        'protocol': 'socks',
        'settings': {
            'servers': [{
                'address': host,
                'port': int(port or '0'),
                'users': [{'user': user, 'pass': password}],
            }]
        }
    }


def build_rules():
    rules = [
        {'inboundTag':['api'],'outboundTag':'api','type':'field'},
        {'type':'field','port':'443','network':'udp','outboundTag':'blocked'},
    ]
    # Game blocking
    rules.append({'type':'field','domain':[
        'domain:battle.net','domain:blizzard.com',
        'domain:gog.com','domain:gog-cdn.com',
        'domain:riotgames.com','domain:leagueoflegends.com',
    ],'outboundTag':'blocked'})

    # Tracker blocking
    rules.append({'type':'field','domain':[
        'domain:google-analytics.com','domain:googletagmanager.com','domain:googletagservices.com',
        'domain:doubleclick.net','domain:googleadservices.com',
        'domain:connect.facebook.net','domain:facebook.net','domain:snap.licdn.com',
        'domain:ads-twitter.com','domain:hotjar.com','domain:crazyegg.com',
        'domain:fullstory.com','domain:scorecardresearch.com','domain:quantserve.com',
        'domain:adnxs.com','domain:criteo.com','domain:taboola.com','domain:outbrain.com',
        'domain:segment.io','domain:mixpanel.com','domain:amplitude.com',
    ],'outboundTag':'blocked'})

    rules.append({'type':'field','domain':GOOGLE_CLOUD_API_EXCEPTIONS,'outboundTag':'direct'})

    rules.extend([
        {'ip':['geoip:private'],'outboundTag':'blocked','type':'field'},
        {'outboundTag':'blocked','protocol':['bittorrent'],'type':'field'},
    ])

    for outbound in STRICT_RESIDENTIAL_CHAIN:
        rules.append({
            'type': 'field',
            'user': GROUP_MEMBERS[outbound],
            'outboundTag': outbound,
        })

    rules.append({'type':'field','network':'tcp,udp','outboundTag':DEFAULT_RESIDENTIAL_OUTBOUND})
    return rules


def apply_routing(cfg, rules):
    if NODE not in FALLBACK_SENDTHROUGH_MAP:
        raise RuntimeError(f'unsupported NODE_ROLE for VPS fallback: {NODE}')

    obs = cfg.get('outbounds', [])
    tags = {o.get('tag') for o in obs}

    node_st = SENDTHROUGH_MAP.get(NODE, SENDTHROUGH_MAP['LA'])
    for rtag in ('residential-r1', 'residential-r2'):
        outbound = next((item for item in obs if item.get('tag') == rtag), None)
        if outbound is None:
            outbound = {'tag': rtag, 'settings': {}}
            obs.append(outbound)
        outbound['protocol'] = 'freedom'
        outbound['settings'] = {'domainStrategy': 'AsIs'}
        outbound['sendThrough'] = node_st[rtag]
    socks_tags = set(RESIDENTIAL_CHAIN) - {'residential-r1', 'residential-r2', *FALLBACK_TAGS}
    for rtag in sorted(socks_tags):
        if rtag in {o.get('tag') for o in obs}:
            continue
        socks = socks_outbound(rtag)
        if not socks:
            raise RuntimeError(f'missing SOCKS5 credentials for {rtag} in {SOCKS_ENV}')
        obs.append(socks)

    fallback_sendthrough = FALLBACK_SENDTHROUGH_MAP[NODE]
    for tag in FALLBACK_TAGS:
        outbound = next((item for item in obs if item.get('tag') == tag), None)
        if outbound is None:
            outbound = {'tag': tag, 'protocol': 'freedom', 'settings': {}}
            obs.append(outbound)
        outbound['protocol'] = 'freedom'
        outbound['settings'] = {'domainStrategy': 'UseIPv4'}
        send_through = fallback_sendthrough[tag]
        if send_through:
            outbound['sendThrough'] = send_through
        else:
            outbound.pop('sendThrough', None)

    direct_ob = next((o for o in obs if o.get('tag') == 'direct'), None)
    if not direct_ob:
        direct_ob = {'tag': 'direct', 'protocol': 'freedom', 'settings': {'domainStrategy': 'UseIPv4'}}
        obs.insert(0, direct_ob)
    else:
        if obs[0].get('tag') != 'direct':
            obs.remove(direct_ob)
            obs.insert(0, direct_ob)
        direct_ob.setdefault('settings', {})['domainStrategy'] = 'UseIPv4'

    if 'blocked' not in {o.get('tag') for o in obs}:
        obs.append({'tag': 'blocked', 'protocol': 'blackhole', 'settings': {}})

    cfg['outbounds'] = obs
    cfg['routing'] = {'domainStrategy': 'IPIfNonMatch', 'rules': rules}
    return cfg


def active_users(cfg):
    users = set()
    for inbound in cfg.get('inbounds', []):
        for client in (inbound.get('settings') or {}).get('clients') or []:
            if client.get('email'):
                users.add(client['email'])
    return users


def validate_config(cfg):
    rules = cfg.get('routing', {}).get('rules', [])
    forbidden = {'residential-r1', 'residential-r2', 'fallback-la', 'fallback-bwh'}
    direct_domains = set(GOOGLE_CLOUD_API_EXCEPTIONS)
    for rule in rules:
        outbound = rule.get('outboundTag')
        if outbound in forbidden:
            raise RuntimeError(f'forbidden VPS or legacy residential route: {outbound}')
        if outbound == 'direct' and set(rule.get('domain') or []) - direct_domains:
            raise RuntimeError('direct rule contains a domain outside the explicit API allowlist')

    for outbound in STRICT_RESIDENTIAL_CHAIN:
        matches = [
            rule for rule in rules
            if set(rule.get('user') or []) == set(GROUP_MEMBERS[outbound])
            and not rule.get('domain')
            and rule.get('outboundTag') == outbound
        ]
        if len(matches) != 1:
            raise RuntimeError(f'expected one strict rule for {outbound}, found {len(matches)}')

    if not rules or rules[-1].get('outboundTag') != DEFAULT_RESIDENTIAL_OUTBOUND:
        raise RuntimeError('unlisted users do not have a residential default')

    unknown = active_users(cfg) - set(ASSIGNED_USERS)
    if unknown:
        print('unlisted_users=' + ','.join(sorted(unknown)))


def main():
    if ASSIGNMENT_POLICY.get('server_direct_domains') != GOOGLE_CLOUD_API_EXCEPTIONS:
        raise RuntimeError('server direct domains do not match the explicit API allowlist')
    print(f'=== Strict residential routing (NODE={NODE}, groups={len(STRICT_RESIDENTIAL_CHAIN)}) ===')

    with open(CONFIG) as f:
        cfg = json.load(f)
    rules = build_rules()
    cfg = apply_routing(cfg, rules)
    validate_config(cfg)

    if dry_run:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=os.path.dirname(CONFIG), delete=False) as handle:
            json.dump(cfg, handle, indent=2)
            preview = handle.name
        result = subprocess.run([XRAY, 'run', '-test', '-c', preview], capture_output=True, text=True)
        os.unlink(preview)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout)[-1200:])
        print('active_users=' + ','.join(sorted(active_users(cfg))))
        print('strict_rules=' + ','.join(f'{tag}:{len(GROUP_MEMBERS[tag])}' for tag in STRICT_RESIDENTIAL_CHAIN))
        print('=== DRY RUN ===')
        return

    backup = f'{CONFIG}.bak-final-{ts}'
    shutil.copy2(CONFIG, backup)

    conn = sqlite3.connect(DB); cur = conn.cursor()
    tmpl_row = cur.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()

    with open(CONFIG,'w') as f:
        json.dump(cfg, f, indent=2)

    result = subprocess.run([XRAY,'run','-test','-c',CONFIG],capture_output=True,text=True)
    if result.returncode != 0:
        print(f'[FAIL] {result.stderr[:300]}')
        shutil.copy2(backup,CONFIG); conn.close(); sys.exit(1)

    if tmpl_row and tmpl_row[0]:
        tmpl = json.loads(tmpl_row[0])
        apply_routing(tmpl, rules)
        cur.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",(json.dumps(tmpl,indent=2),))
        conn.commit()
        print('[PERSIST] template updated')
    else:
        print('[PERSIST] v3.4.0 node (no SQLite template needed)')
    conn.close()

    with open(GROUPS_PATH,'w') as f:
        json.dump({
            '_updated': ts,
            '_arch': 'STRICT RESIDENTIAL V1: per-client R3-R4-R5 with no VPS fallback',
            'GOOGLE_CLOUD_API_EXCEPTIONS': {'domains': GOOGLE_CLOUD_API_EXCEPTIONS, 'outbound': 'direct'},
            'CLIENT_RESIDENTIAL': {
                outbound: {
                    'members': GROUP_MEMBERS[outbound],
                    'outbound': outbound,
                    'failover_chain': list(STRICT_RESIDENTIAL_CHAIN),
                }
                for outbound in STRICT_RESIDENTIAL_CHAIN
            },
            'PAYMENT': {
                'domains': PAYMENT_DOMAINS,
                'excluded_client_direct_domains': ASSIGNMENT_POLICY.get('client_direct_domains', []),
                'outbound': 'per-client-assignment',
                'failover_chain': list(STRICT_RESIDENTIAL_CHAIN),
            },
            'DEFAULT_UNASSIGNED': {
                'outbound': DEFAULT_RESIDENTIAL_OUTBOUND,
                'failover_chain': list(STRICT_RESIDENTIAL_CHAIN),
            },
        }, f, indent=2)

    restart = subprocess.run(['x-ui','restart'],capture_output=True,text=True,timeout=45)
    if restart.returncode != 0:
        raise RuntimeError(restart.stderr[-1200:])
    time.sleep(3)

    pgrep = subprocess.run(['pgrep','-f','xray'],capture_output=True,text=True)
    pids = [p for p in pgrep.stdout.strip().split('\n') if p]
    ss = subprocess.run(['ss','-H','-tlnp'],capture_output=True,text=True)
    listeners = [l for l in ss.stdout.split('\n') if any(p in l for p in [':443', ':8443', ':2083'])]

    if pids and len(listeners) >= 3:
        print(f'[DONE] pids={pids} listeners={len(listeners)}')
    else:
        print('[ERROR] rollback')
        shutil.copy2(backup,CONFIG)
        conn2 = sqlite3.connect(DB)
        conn2.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",(tmpl_row[0],))
        conn2.commit(); conn2.close()
        subprocess.run(['x-ui','restart'],capture_output=True,timeout=45)
        sys.exit(1)


if __name__ == '__main__':
    main()
