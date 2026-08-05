#!/usr/bin/env python3
"""
住宅 IP 自动故障切换。
严格 client 分组只使用 R3 -> R4 -> R5；三条住宅线路都不可用时保持失败。
"""
import json, sqlite3, shutil, subprocess, sys, os, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

CONFIG = '/usr/local/x-ui/bin/config.json'
DB = '/etc/x-ui/x-ui.db'
XRAY = '/usr/local/x-ui/bin/xray-linux-amd64'
STATE_FILE = '/etc/x-ui/residential-failover-state.json'
LOG_FILE = '/var/log/residential-failover.log'
POLICY_FILE = os.environ.get(
    'CLIENT_RESIDENTIAL_POLICY_FILE',
    '/etc/x-ui/client-residential-assignments.json',
)
FAIL_THRESHOLD = 3
RECOVERY_THRESHOLD = 5
PROBE_TIMEOUT = 8
PROBE_URL = 'https://api.ipify.org'
SOCKS_ENV = os.environ.get('RESIDENTIAL_SOCKS_ENV', '/etc/x-ui/residential-socks.env')


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

LA_PUBLIC_IP = os.environ.get('LA_PUBLIC_IP', '64.186.226.51')
BWH_PUBLIC_IP = os.environ.get('BWH_PUBLIC_IP', '174.137.51.201')
R1_PUBLIC_IP = os.environ.get('R1_PUBLIC_IP', '192.204.62.110')
R2_PUBLIC_IP = os.environ.get('R2_PUBLIC_IP', '38.150.34.205')
FALLBACK_WG_LA_IP = os.environ.get('FALLBACK_WG_LA_IP', '10.10.18.1')
FALLBACK_WG_BWH_IP = os.environ.get('FALLBACK_WG_BWH_IP', '10.10.18.2')


def detect_node():
    configured = os.environ.get('NODE_ROLE', '').upper()
    if configured in {'LA', 'BWH'}:
        return configured
    result = subprocess.run(['ip', '-4', '-o', 'addr', 'show'], capture_output=True, text=True)
    if LA_PUBLIC_IP in result.stdout:
        return 'LA'
    if BWH_PUBLIC_IP in result.stdout:
        return 'BWH'
    raise RuntimeError('unable to detect NODE_ROLE for VPS fallback')


NODE = detect_node()

WG = {
    'r1': {'iface':'wg0', 'expected_ip':R1_PUBLIC_IP},
    'r2': {'iface':'wg1', 'expected_ip':R2_PUBLIC_IP},
}

STRICT_RESIDENTIAL_CHAIN = ['residential-r3', 'residential-r4', 'residential-r5']


def load_assignment_groups():
    with open(POLICY_FILE) as handle:
        policy = json.load(handle)
    if policy.get('failover_order') != STRICT_RESIDENTIAL_CHAIN:
        raise RuntimeError('strict failover policy must be R3/R4/R5')
    groups = policy.get('groups') or {}
    if set(groups) != set(STRICT_RESIDENTIAL_CHAIN):
        raise RuntimeError('strict failover groups are incomplete')
    members_by_group = {}
    assigned = set()
    for outbound in STRICT_RESIDENTIAL_CHAIN:
        members = list(groups[outbound].get('members') or [])
        for member, target in (policy.get('additional_assignments') or {}).items():
            if target == outbound:
                members.append(member)
        if not members or len(members) != len(set(members)):
            raise RuntimeError(f'invalid members for {outbound}')
        if assigned.intersection(members):
            raise RuntimeError('client appears in multiple strict groups')
        assigned.update(members)
        members_by_group[outbound] = members
    return members_by_group


GROUP_MEMBERS = load_assignment_groups()


def group_candidates(primary):
    return [primary] + [candidate for candidate in STRICT_RESIDENTIAL_CHAIN if candidate != primary]


GROUPS = {
    f'CLIENT_{outbound.rsplit("-", 1)[-1].upper()}': {
        'type': 'user',
        'members': GROUP_MEMBERS[outbound],
        'primary': outbound,
        'candidates': group_candidates(outbound),
    }
    for outbound in STRICT_RESIDENTIAL_CHAIN
}

RESOURCE_KEYS = ('r1','r2','r3','r4','r5','la','bwh')

FALLBACK_PROBES = {
    'LA': {
        'la': {'interface': 'eth0', 'expected_ip': LA_PUBLIC_IP},
        'bwh': {'interface': FALLBACK_WG_LA_IP, 'expected_ip': BWH_PUBLIC_IP},
    },
    'BWH': {
        'la': {'interface': FALLBACK_WG_BWH_IP, 'expected_ip': LA_PUBLIC_IP},
        'bwh': {'interface': 'eth0', 'expected_ip': BWH_PUBLIC_IP},
    },
}


def load_commute_state():
    commute_file = '/etc/x-ui/commute-state.json'
    if os.path.exists(commute_file):
        try:
            import json
            with open(commute_file) as f:
                state = json.load(f)
                return state.get('is_swapped', False)
        except: pass
    return False

dry_run = '--dry-run' in sys.argv


def log(msg):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(LOG_FILE,'a') as f: f.write(line+'\n')
    except: pass


def probe_wireguard(key):
    iface = WG[key]['iface']
    expected_ip = WG[key]['expected_ip']
    try:
        r = subprocess.run(['curl','-4','-s','--max-time',str(PROBE_TIMEOUT),'--interface',iface,PROBE_URL],
                           capture_output=True,text=True,timeout=PROBE_TIMEOUT+2)
        ip = r.stdout.strip()
        return ip == expected_ip, f'{key} via {iface} -> {ip or "empty"}'
    except Exception as e:
        return False, f'{key} via {iface} -> {str(e)[:60]}'


def probe_socks(key):
    prefix = 'LYCHEE_' + key.upper()
    host = os.environ.get(prefix + '_HOST') or os.environ.get(prefix + '_PUBLIC_IP')
    expected_ip = os.environ.get(prefix + '_PUBLIC_IP') or host
    port = os.environ.get(prefix + '_PORT')
    user = os.environ.get(prefix + '_USERNAME')
    password = os.environ.get(prefix + '_PASSWORD')
    if not all([host, expected_ip, port, user, password]):
        return False, f'{key} SOCKS5 -> credentials missing'
    try:
        result = subprocess.run([
            'curl','-4','--fail','--silent','--show-error','--max-time',str(PROBE_TIMEOUT),
            '--socks5-hostname',f'{host}:{port}','--proxy-user',f'{user}:{password}',PROBE_URL,
        ], capture_output=True, text=True, timeout=PROBE_TIMEOUT + 2)
        actual_ip = result.stdout.strip()
        ok = result.returncode == 0 and actual_ip == expected_ip
        return ok, f'{key} SOCKS5 -> {actual_ip or "empty"}'
    except Exception as e:
        return False, f'{key} SOCKS5 -> {str(e)[:60]}'


def probe_fallback(key):
    settings = FALLBACK_PROBES[NODE][key]
    interface = settings['interface']
    expected_ip = settings['expected_ip']
    try:
        result = subprocess.run([
            'curl','-4','--fail','--silent','--show-error','--max-time',str(PROBE_TIMEOUT),
            '--interface',interface,PROBE_URL,
        ], capture_output=True, text=True, timeout=PROBE_TIMEOUT + 2)
        actual_ip = result.stdout.strip()
        ok = result.returncode == 0 and actual_ip == expected_ip
        return ok, f'{key} via {interface} -> {actual_ip or "empty"}'
    except Exception as error:
        return False, f'{key} via {interface} -> {str(error)[:60]}'


def probe(key):
    if key in WG:
        return probe_wireguard(key)
    if key in {'la', 'bwh'}:
        return probe_fallback(key)
    return probe_socks(key)


def load_state() -> dict[str, Any]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f: return json.load(f)
    return {
        '_updated': datetime.now(timezone.utc).isoformat(),
        'r1': {'fail_count':0,'success_count':0,'healthy':True},
        'r2': {'fail_count':0,'success_count':0,'healthy':True},
        'outbounds': {g: d['candidates'][0] for g,d in GROUPS.items()},
        'switch_history': [],
    }


def save_state(s: dict[str, Any]):
    s['_updated'] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(STATE_FILE),exist_ok=True)
    with open(STATE_FILE,'w') as f: json.dump(s,f,indent=2)


def ensure_state_shape(state: dict[str, Any]) -> dict[str, Any]:
    for key in RESOURCE_KEYS:
        state.setdefault(key, {'fail_count':0,'success_count':0,'healthy':False})
    state.setdefault('outbounds', {})
    state.setdefault('switch_history', [])
    return state


def find_rule(rules, gd):
    for i,r in enumerate(rules):
        if gd['type'] == 'user':
            u = set(r.get('user') or [])
            if set(gd['members']).issubset(u) and not r.get('domain'):
                return i,r
    return None,None


def change_outbound(cfg, gid, gd, new_ob):
    rules = cfg.get('routing',{}).get('rules',[])
    idx,rule = find_rule(rules, gd)
    if rule is None:
        log(f'  WARN: {gid} rule not found')
        return False
    old = rule.get('outboundTag')
    if old == new_ob: return False
    rule['outboundTag'] = new_ob
    log(f'  rule[{idx}] {gid}: {old} -> {new_ob}')
    return True


def current_outbound(cfg, gd):
    _, rule = find_rule(cfg.get('routing',{}).get('rules',[]), gd)
    return rule.get('outboundTag') if rule else None


def do_switches(switches):
    for gid, _, from_ob, to_ob, reason in switches:
        log(f'SWITCH: {gid} {from_ob}->{to_ob} ({reason})')
    if dry_run:
        return True
    ts = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    db_backup = f'{DB}.bak-fo-{ts}'
    shutil.copy2(DB, db_backup)
    conn = None
    preview_path = None
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
        outbound_tags = {item.get('tag') for item in cfg.get('outbounds',[])}
        for gid, gd, _, to_ob, _ in switches:
            if to_ob not in outbound_tags:
                raise RuntimeError(f'outbound not found: {to_ob}')
            if not change_outbound(cfg, gid, gd, to_ob):
                raise RuntimeError(f'rule not changed: {gid}')
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', prefix='xray-failover-', dir=os.path.dirname(CONFIG), delete=False
        ) as preview:
            json.dump(cfg, preview, indent=2)
            preview_path = preview.name
        result = subprocess.run([XRAY,'run','-test','-c',preview_path],capture_output=True,text=True)
        if result.returncode != 0:
            raise RuntimeError('xray config test failed')
        conn = sqlite3.connect(DB)
        row = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
        if not row or not row[0]:
            raise RuntimeError('xrayTemplateConfig not found')
        template = json.loads(row[0])
        for gid, gd, _, to_ob, _ in switches:
            if not change_outbound(template, gid, gd, to_ob):
                raise RuntimeError(f'template rule not changed: {gid}')
        conn.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",(json.dumps(template,indent=2),))
        conn.commit()
        conn.close()
        conn = None
        restart = subprocess.run(['x-ui','restart'],capture_output=True,text=True,timeout=45)
        if restart.returncode != 0:
            raise RuntimeError('x-ui restart failed')
        time.sleep(3)
        process = subprocess.run(['pgrep','-f','xray-linux-amd64'],capture_output=True,text=True)
        if not process.stdout.strip():
            raise RuntimeError('xray process missing after restart')
        with open(CONFIG) as f:
            live = json.load(f)
        for gid, gd, _, to_ob, _ in switches:
            if current_outbound(live, gd) != to_ob:
                raise RuntimeError(f'generated config did not persist switch: {gid}')
    except Exception as error:
        log(f'  ERROR rollback: {error}')
        if conn is not None:
            conn.close()
        shutil.copy2(db_backup,DB)
        subprocess.run(['x-ui','restart'],capture_output=True,timeout=45)
        return False
    finally:
        if preview_path and os.path.exists(preview_path):
            os.unlink(preview_path)
    log(f'  DONE: {len(switches)} groups switched with one restart')
    return True


def main():
    log(f'=== Check (node={NODE} dry_run={dry_run}) ===')
    state = ensure_state_shape(load_state())

    with ThreadPoolExecutor(max_workers=len(RESOURCE_KEYS)) as executor:
        probe_results = dict(zip(RESOURCE_KEYS, executor.map(probe, RESOURCE_KEYS)))

    for key in RESOURCE_KEYS:
        ok, detail = probe_results[key]
        log(f'  {detail}')
        s = state[key]
        s['fail_count'] = 0 if ok else s['fail_count']+1
        s['success_count'] = s['success_count']+1 if ok else 0
        was = s['healthy']
        if s['fail_count'] >= FAIL_THRESHOLD: s['healthy'] = False
        if s['success_count'] >= RECOVERY_THRESHOLD: s['healthy'] = True
        if was != s['healthy']:
            log(f'  {key.upper()} {was}->{s["healthy"]} (f={s["fail_count"]} s={s["success_count"]})')

    with open(CONFIG) as f:
        cfg = json.load(f)
    switches = []
    for gid, gd in GROUPS.items():
        current = current_outbound(cfg, gd)
        if current:
            state['outbounds'][gid] = current
        target = next((candidate for candidate in gd['candidates']
                       if state[candidate.rsplit('-',1)[-1]]['healthy']), None)
        if not target:
            log(f'  WARN: {gid} has no healthy candidate; keeping {current}')
            continue
        if current == target:
            continue
        reason = 'preferred healthy chain: ' + ' -> '.join(gd['candidates'])
        switches.append((gid, gd, current, target, reason))

    if switches and do_switches(switches):
        for gid, _, current, target, reason in switches:
            state['outbounds'][gid] = target
            state['switch_history'].append({
                'time':datetime.now(timezone.utc).isoformat(),
                'group':gid,'from':current,'to':target,'reason':reason,
            })
        state['switch_history'] = state['switch_history'][-20:]

    if not dry_run:
        save_state(state)
    obs = state.get('outbounds',{})
    parts = []
    for g in GROUPS:
        parts.append(g + '=' + obs.get(g, '?'))
    log('=== Done. ' + ' '.join(parts) + ' ===')


if __name__ == '__main__': main()
