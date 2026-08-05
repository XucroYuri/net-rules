#!/usr/bin/env python3
import json
import os
import shutil
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


CONFIG = Path('/usr/local/x-ui/bin/config.json')
DB = Path('/etc/x-ui/x-ui.db')
XRAY = Path('/usr/local/x-ui/bin/xray-linux-amd64')
TAG = 'residential-r6'
DRY_RUN = '--dry-run' in sys.argv
REMOVE = '--remove' in sys.argv
PROBE = '--probe' in sys.argv


def option_value(name):
    prefix = name + '='
    for argument in sys.argv[1:]:
        if argument.startswith(prefix):
            return argument[len(prefix):]
    return ''


BIND_USER = option_value('--bind-user')
UNBIND_USER = option_value('--unbind-user')
if BIND_USER and UNBIND_USER:
    raise RuntimeError('bind and unbind cannot be used together')


def load_env_file(path='/etc/x-ui/residential-socks.env'):
    if not os.path.exists(path):
        return
    with open(path) as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


def timestamp():
    return datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')


def load_json(path):
    with path.open() as handle:
        return json.load(handle)


def r6_outbound():
    protocol = os.environ.get('LYCHEE_R6_PROTOCOL', 'socks5')
    host = os.environ.get('LYCHEE_R6_HOST') or os.environ.get('LYCHEE_R6_PUBLIC_IP')
    port = os.environ.get('LYCHEE_R6_PORT')
    user = os.environ.get('LYCHEE_R6_USERNAME')
    password = os.environ.get('LYCHEE_R6_PASSWORD')
    if protocol != 'socks5' or not all([host, port, user, password]):
        raise RuntimeError('R6 SOCKS5 environment is incomplete')
    port_value = port or ''
    return {
        'tag': TAG,
        'protocol': 'socks',
        'settings': {
            'servers': [{
                'address': host,
                'port': int(port_value),
                'users': [{'user': user, 'pass': password}],
            }],
        },
    }


def upsert_outbound(config, outbound):
    outbounds = config.setdefault('outbounds', [])
    matches = [item for item in outbounds if item.get('tag') == TAG]
    if len(matches) > 1:
        raise RuntimeError('duplicate residential-r6 outbounds')
    if matches:
        index = outbounds.index(matches[0])
        outbounds[index] = outbound
        return True
    outbounds.append(outbound)
    return True


def remove_outbound(config):
    config['outbounds'] = [item for item in config.get('outbounds', []) if item.get('tag') != TAG]


def validate_no_route_reference(config, allowed_user=None):
    for rule in config.get('routing', {}).get('rules', []):
        if rule.get('outboundTag') == TAG and rule.get('user') != [allowed_user]:
            raise RuntimeError('R6 must remain unbound from client routing rules')


def active_users(config):
    users = set()
    for inbound in config.get('inbounds', []):
        for client in (inbound.get('settings') or {}).get('clients') or []:
            if client.get('email'):
                users.add(client['email'])
    return users


def remove_user_override(config, user):
    rules = config.setdefault('routing', {}).setdefault('rules', [])
    config['routing']['rules'] = [
        rule for rule in rules
        if not (rule.get('outboundTag') == TAG and rule.get('user') == [user])
    ]


def add_user_override(config, user):
    remove_user_override(config, user)
    rules = config.setdefault('routing', {}).setdefault('rules', [])
    index = next(
        (i for i, rule in enumerate(rules) if user in (rule.get('user') or []) and not rule.get('domain')),
        len(rules),
    )
    rules.insert(index, {
        'type': 'field',
        'user': [user],
        'outboundTag': TAG,
    })


def xray_test(config):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', prefix='xray-r6-test-', delete=False
        ) as handle:
            json.dump(config, handle, indent=2)
            temp_path = handle.name
        result = subprocess.run(
            [str(XRAY), 'run', '-test', '-c', temp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout)[-1600:])
    finally:
        if temp_path:
            os.unlink(temp_path)


def probe_xray(outbound):
    expected = os.environ.get('LYCHEE_R6_PUBLIC_IP')
    if not expected:
        raise RuntimeError('R6 public IP is missing')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    config = {
        'log': {'loglevel': 'warning'},
        'inbounds': [{
            'listen': '127.0.0.1',
            'port': port,
            'protocol': 'socks',
            'settings': {'auth': 'noauth', 'udp': False},
        }],
        'outbounds': [outbound],
    }
    temp_path = None
    process = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', prefix='xray-r6-probe-', delete=False
        ) as handle:
            json.dump(config, handle, indent=2)
            temp_path = handle.name
        process = subprocess.Popen(
            [str(XRAY), 'run', '-c', temp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        result = subprocess.run(
            [
                'curl', '-4', '--fail', '--silent', '--show-error', '--max-time', '20',
                '--socks5-hostname', f'127.0.0.1:{port}', 'https://api.ipify.org',
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
        actual = result.stdout.strip()
        if result.returncode != 0 or actual != expected:
            raise RuntimeError(f'R6 Xray probe failed: {actual or result.stderr[-400:]}')
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if temp_path:
            os.unlink(temp_path)


def load_template(connection):
    row = connection.execute(
        "SELECT value FROM settings WHERE key='xrayTemplateConfig'"
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError('xrayTemplateConfig not found')
    return json.loads(row[0]), row[0]


def restart_and_verify(before_routing):
    result = subprocess.run(['x-ui', 'restart'], capture_output=True, text=True, timeout=45)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-1600:])
    time.sleep(3)
    live = load_json(CONFIG)
    validate_no_route_reference(live, BIND_USER)
    if TAG not in {item.get('tag') for item in live.get('outbounds', [])}:
        raise RuntimeError('residential-r6 missing after x-ui restart')
    if live.get('routing') != before_routing:
        raise RuntimeError('routing rules changed while adding isolated R6 outbound')
    xray_test(live)


def main():
    load_env_file()
    live_before = load_json(CONFIG)
    allowed_user_before = BIND_USER or UNBIND_USER
    validate_no_route_reference(live_before, allowed_user_before)
    target_user = BIND_USER or UNBIND_USER
    if target_user and target_user not in active_users(live_before):
        raise RuntimeError(f'active client not found: {target_user}')
    test_live = json.loads(json.dumps(live_before))
    if REMOVE:
        remove_outbound(test_live)
    else:
        upsert_outbound(test_live, r6_outbound())
    if BIND_USER:
        add_user_override(test_live, BIND_USER)
    elif UNBIND_USER:
        remove_user_override(test_live, UNBIND_USER)
    validate_no_route_reference(test_live, BIND_USER)
    xray_test(test_live)

    connection = sqlite3.connect(DB)
    template, original_template = load_template(connection)
    if REMOVE:
        remove_outbound(template)
    else:
        upsert_outbound(template, r6_outbound())
    if BIND_USER:
        add_user_override(template, BIND_USER)
    elif UNBIND_USER:
        remove_user_override(template, UNBIND_USER)
    validate_no_route_reference(template, BIND_USER)

    print(f'tag={TAG}')
    print(f'action={"remove" if REMOVE else "add"}')
    print(f'dry_run={DRY_RUN}')
    print(f'user_override={BIND_USER or ("removed:" + UNBIND_USER if UNBIND_USER else "none")}')
    print(f'routing_reference={"user-only" if BIND_USER else "none"}')
    print('xray_preflight=PASS')
    if DRY_RUN:
        if PROBE and not REMOVE:
            probe_xray(r6_outbound())
            print('xray_r6_probe=PASS')
        connection.close()
        return

    ts = timestamp()
    db_backup = DB.with_name(f'{DB.name}.bak-r6-{ts}')
    config_backup = CONFIG.with_name(f'{CONFIG.name}.bak-r6-{ts}')
    shutil.copy2(DB, db_backup)
    shutil.copy2(CONFIG, config_backup)
    try:
        connection.execute(
            "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
            (json.dumps(template, indent=2),),
        )
        connection.commit()
        connection.close()
        restart_and_verify(test_live.get('routing'))
        if PROBE and not REMOVE:
            probe_xray(r6_outbound())
            print('xray_r6_probe=PASS')
    except Exception:
        try:
            connection.close()
        except Exception:
            pass
        shutil.copy2(db_backup, DB)
        subprocess.run(['x-ui', 'restart'], capture_output=True, text=True, timeout=45)
        raise
    print(f'db_backup={db_backup}')
    print(f'config_backup={config_backup}')
    print('status=applied')


if __name__ == '__main__':
    main()
