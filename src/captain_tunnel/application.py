# -*- coding: utf-8 -*-
"""캡틴 터널링 — 폐쇄망 PC 용 SSH Local port forwarding 관리 프로그램 (MobaSSHTunnel 대체).

경유 SSH 서버 1곳에 SSH 접속 1회를 맺고, 그 위로 여러 개의 Local port forwarding(-L)을 연다.
- 터널별 시작/중지, 상태·연결수·송수신량 표시
- 끊기면 자동 재연결(로컬 포트는 계속 열어 두고, 재연결 동안 들어온 접속은 최대 20초 대기)
- 비밀번호 기억(Windows DPAPI 로 현재 사용자만 복호화) · 키 파일 인증
- 포트 점유 프로그램(PID/이름) 표시, 대상 연결 시험
- Windows 서비스(CaptainTunnel)로 설치하면 PC 시작 시(로그인 전) 자동으로 '자동(✓)' 터널을 열고 유지.
  화면은 관리 프로그램 역할: 서비스 설치·시작·중지·제거, 상태 표시, 터널 편집(서비스가 설정 변경을 자동 반영)
설정: 배포 실행은 exe 옆, 개발 실행은 프로젝트 data/ (쓸 수 없으면 %LOCALAPPDATA%\\Captain-Tunnel)

명령행: (없음)=화면 · --autostart --minimized=로그인 자동 실행용 · --headless=창 없이 터널 유지(시험·문제 확인용)
        --service-run=서비스 관리자(SCM)가 실행 · --service install|uninstall|start|stop=관리자 권한으로 서비스 관리
"""
import base64
import configparser
import ctypes
import datetime as dt
import hashlib
import json
import logging
import os
import queue
import re
import select
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from tkinter import filedialog, messagebox, ttk

import paramiko
from captain_tunnel.configuration import validate_config
from captain_tunnel.paths import data_dir, executable_dir, icon_path, source_launcher

try:
    from . import icons_data      # tools\make_icons.py 가 만든 PNG 아이콘(base64) — 없으면 글자만 표시
except ImportError:
    icons_data = None

# paramiko 내부 경고(채널 열기 실패 등)는 화면 로그로 따로 남기므로 stderr 로 내보내지 않는다(창 없는 exe 대비)
logging.getLogger("paramiko").addHandler(logging.NullHandler())
logging.getLogger("paramiko").propagate = False

from captain_tunnel import __version__ as VERSION
SCALE = 1.0          # 고DPI 배율(main 에서 계산)
APP_NAME = "캡틴 터널링"
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0

DEFAULT_CONFIG = {
    "server": {
        "host": "172.17.210.158",
        "port": 5222,
        "user": "cloud",
        "auth": "password",          # password | key
        "key_file": "",
        "remember_password": False,
        "password_dpapi": "",        # 화면용 — 현재 사용자 DPAPI
        "password_svc": "",          # 서비스용 — PC(머신) 범위 DPAPI (로그인 전 SYSTEM 이 복호화)
        "keepalive_interval": 30,
        "keepalive_count": 3,
    },
    "autostart_on_launch": False,
    "tunnels": [],                   # 아래 STD_TUNNELS
}

# 표준 터널 목록 — 이름 = [구분] 망 내용. 구분 DB · SSH · WEB, 망 공사망 · 개발망 · MIG
# · 공사망 SSH/WEB · 공사망 DB 2건: MobaXterm 캡처(2026-10-07) + 현장 추가(8090)
# · [DB] 나머지: 에어플로 커넥션(2026-10-27 캡처). 내용은 DAG 이름({org|mig|dev|prd}_스키마) 을 따름
#   172.17.155.x = 공사망, 그 밖(172.17.209/210/251.x) = 개발망. 원격 포트가 겹치면 내 PC 포트를 +1, +2
# 기존 설정에는 [도구 → 표준 터널 목록 반영] 으로 원격 대상이 같은 터널은 이름을 바꾸고 없는 것은 추가한다.
STD_TUNNELS = [
    # (이름, 내 PC 포트, 원격 주소, 원격 포트, 자동)
    ("[DB] 개발망 dev_fmsown 시설정보 (Tibero IFMFS)", 38661, "172.17.210.153", 38660, False),
    ("[DB] 개발망 dev_fmsown 항행정보 (Tibero IFMNS)", 38652, "172.17.210.153", 38650, False),
    ("[DB] 개발망 dev_gisown (PostgreSQL)", 38734, "172.17.209.138", 38733, False),
    ("[DB] 개발망 mig_fclown·nstown·cfms (Tibero IFMDAT)", 38650, "172.17.210.158", 38650, False),
    ("[DB] 개발망 mig_pg migadm·migfac (PostgreSQL)", 38733, "172.17.209.139", 38733, False),
    ("[DB] 개발망 org_cub cfmdb (CUBRID)", 30000, "172.17.251.228", 30000, False),
    ("[DB] 개발망 org_ora ufmsadmin·ufmsfac·airphoto (Oracle)", 21521, "172.17.209.138", 21521, False),
    ("[DB] 공사망 prd_fmsown 시설정보 (Tibero IFMFS)", 38660, "172.17.155.113", 38660, True),
    ("[DB] 공사망 prd_fmsown 항행정보 (Tibero IFMNS)", 38651, "172.17.155.115", 38650, True),
    ("[DB] 공사망 prd_gisown (PostgreSQL)", 37733, "172.17.155.117", 37733, False),
    ("[SSH] 공사망 매퍼배포", 9222, "172.17.155.132", 5222, True),
    ("[SSH] 공사망 자료적재", 8222, "172.17.155.113", 5222, True),
    ("[WEB] MIG 매퍼 및 에어플로 운영", 8080, "127.0.0.1", 8080, True),
    ("[WEB] 공사망 매퍼 운영", 8090, "172.17.155.132", 8090, True),
]
DEFAULT_CONFIG["tunnels"] = [{"name": n, "local_port": lp, "remote_host": rh, "remote_port": rp, "auto": a}
                             for n, lp, rh, rp, a in STD_TUNNELS]


def name_sort_key(name):
    """이름 정렬 키 — 대소문자 무시, 숫자는 크기순(포트 8090 < 38650)."""
    return [(0, int(s), "") if s.isdigit() else (1, 0, s.casefold())
            for s in re.split(r"(\d+)", name or "") if s]


def merge_std_tunnels(tunnels):
    """표준 터널 목록을 기존 목록에 반영(제자리 수정). 원격 대상이 같으면 이름만 표준으로(포트 · 자동은 그대로),
    없으면 추가(내 PC 포트가 이미 쓰이면 건너뜀). 반환 (이름 변경 [(옛, 새)], 추가 [이름], 건너뜀 [(이름, 사유)])."""
    renamed, added, skipped = [], [], []
    for n, lp, rh, rp, a in STD_TUNNELS:
        same = [t for t in tunnels if t["remote_host"] == rh and int(t["remote_port"]) == rp]
        if same:
            for t in same:
                if t["name"] != n:
                    renamed.append((t["name"], n))
                    t["name"] = n
            continue
        used = next((t for t in tunnels if int(t["local_port"]) == lp), None)
        if used:
            skipped.append((n, f"내 PC 포트 {lp} 를 '{used['name']}' 가 사용"))
            continue
        tunnels.append({"name": n, "local_port": lp, "remote_host": rh, "remote_port": rp,
                        "bind": "127.0.0.1", "auto": a})
        added.append(n)
    return renamed, added, skipped


# ----------------------------------------------------------------- 경로 · 설정
def _writable(d):
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, ".w")
        with open(p, "w") as f:
            f.write("1")
        os.remove(p)
        return True
    except OSError:
        return False


FROZEN = getattr(sys, "frozen", False)
EXE_DIR = executable_dir()


def app_dir():
    preferred = data_dir()
    if _writable(preferred):
        return preferred
    alt = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Captain-Tunnel")
    os.makedirs(alt, exist_ok=True)
    return alt


APP_DIR = app_dir()
CONF_PATH = os.path.join(APP_DIR, "captain_tunnel.json")
KNOWN_HOSTS = os.path.join(APP_DIR, "known_hosts")
LOG_DIR = os.path.join(APP_DIR, "logs")
STATUS_PATH = os.path.join(APP_DIR, "service_status.json")   # 서비스 → 화면 상태 전달
INI_PATH = os.path.join(APP_DIR, "config.ini")                # 망별 표시 스위치(사람이 메모장으로 고침)

INI_DEFAULT = """; 캡틴 터널링 표시 설정 — 메모장으로 수정 · 저장한 후 화면을 다시 실행하십시오(서비스는 2초 이내 반영).
; true 면 목록에 표시하고 사용합니다. false 면 목록 · 전체 시작 · 서비스에서 제외합니다
; (설정 파일에는 그대로 남아 있어 true 로 바꾸면 다시 표시됩니다).
[tunnels]
; MIG    — 이름에 'MIG' 또는 원격 대상 127.0.0.1(경유 SSH 서버 자신)
show_mig = true
; 개발망 — 이름에 '개발망' (그 밖의 대상도 개발망으로 봅니다)
show_dev = true
; 공사망 — 이름에 '공사망' 또는 원격 대상 172.17.155.x
show_prd = false
"""
NETS = ("mig", "dev", "prd")
NET_LABEL = {"mig": "MIG", "dev": "개발망", "prd": "공사망"}
NET_DEFAULT = {"mig": True, "dev": True, "prd": False}


def tunnel_net(t):
    """터널이 속한 망 — mig · dev · prd."""
    name = t["name"]
    if "공사망" in name or t["remote_host"].startswith("172.17.155."):
        return "prd"
    if "개발망" in name:
        return "dev"
    if "MIG" in name.split()[:2] or t["remote_host"] == "127.0.0.1":
        return "mig"
    return "dev"


def _ini_text(show):
    s = INI_DEFAULT
    for k in NETS:
        s = re.sub(rf"(?m)^show_{k} = \w+$", f"show_{k} = {'true' if show[k] else 'false'}", s)
    return s


def show_nets():
    """config.ini [tunnels] show_mig · show_dev · show_prd — 없거나 값이 잘못되면 기본값(mig · dev true, prd false).
    예전 판의 show_gongsa 는 show_prd 로 읽고, 파일을 새 형식으로 바꿔 쓴다(값은 유지)."""
    p = configparser.ConfigParser()
    try:
        p.read(INI_PATH, encoding="utf-8-sig")
    except (configparser.Error, OSError):
        return dict(NET_DEFAULT)
    sec = p["tunnels"] if p.has_section("tunnels") else {}
    show = {}
    for k in NETS:
        key = f"show_{k}"
        if k == "prd" and key not in sec and "show_gongsa" in sec:
            key = "show_gongsa"
        try:
            show[k] = p.getboolean("tunnels", key, fallback=NET_DEFAULT[k])
        except (configparser.Error, ValueError):
            show[k] = NET_DEFAULT[k]
    if not os.path.exists(INI_PATH) or any(f"show_{k}" not in sec for k in NETS):
        try:                                   # 없거나 예전 형식이면 새 형식으로(현재 값 유지)
            with open(INI_PATH, "w", encoding="utf-8") as f:
                f.write(_ini_text(show))
        except OSError:
            pass
    return show


def ensure_ini():
    show_nets()

SVC_NAME = "CaptainTunnel"
SVC_DISPLAY = "Captain Tunneling (SSH 터널 유지)"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "CaptainTunnel"


def read_config():
    """설정 읽기(오류는 예외로). 없으면 기본값."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH, encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict) or not isinstance(user.get("server", {}), dict):
            raise ValueError("설정과 server는 JSON 객체여야 합니다.")
        if "tunnels" in user and not isinstance(user["tunnels"], list):
            raise ValueError("tunnels는 JSON 배열이어야 합니다.")
        cfg["server"].update(user.get("server", {}))
        cfg["autostart_on_launch"] = bool(user.get("autostart_on_launch", False))
        if isinstance(user.get("tunnels"), list):
            cfg["tunnels"] = user["tunnels"]
    validate_config(cfg)
    # 망별 숨김 — 화면 · 서비스는 cfg["tunnels"] 만 본다. 숨긴 터널은 hidden_tunnels 로 들고 있다가 저장 때 되돌린다
    cfg["show"] = show_nets()
    hide = [t for t in cfg["tunnels"] if not cfg["show"][tunnel_net(t)]]
    cfg["tunnels"] = [t for t in cfg["tunnels"] if not any(t is h for h in hide)]
    cfg["hidden_tunnels"] = hide
    return cfg


def load_config():
    try:
        return read_config()
    except (OSError, ValueError, KeyError, TypeError) as e:
        bak = CONF_PATH + ".broken_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            os.replace(CONF_PATH, bak)
        except OSError:
            pass
        messagebox.showwarning(APP_NAME, f"설정 파일을 읽지 못해 기본값으로 시작합니다.\n{e}\n\n원본: {bak}")
        return read_config()


def save_config(cfg):
    out = {k: v for k, v in cfg.items() if k not in ("show", "hidden_tunnels")}
    out["tunnels"] = list(cfg["tunnels"]) + list(cfg.get("hidden_tunnels", []))
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONF_PATH)


# ----------------------------------------------------------------- DPAPI
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool, machine=False) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    inb = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if protect:
        # 0x1 UI 금지 · 0x4 LOCAL_MACHINE(이 PC 의 모든 계정이 복호화 — 서비스용)
        ok = fn(ctypes.byref(inb), "captain-tunnel", None, None, None, 0x1 | (0x4 if machine else 0), ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(inb), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI 실패")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect_password(pw, machine=False):
    return base64.b64encode(_dpapi(pw.encode("utf-8"), True, machine)).decode("ascii")


def unprotect_password(s):
    return _dpapi(base64.b64decode(s), False).decode("utf-8")


# ----------------------------------------------------------------- 포트 점유 확인
def port_free(bind, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if IS_WIN:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind((bind, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def port_owner(port):
    """LISTENING 중인 프로세스 'PID 1234 (name.exe)' — 못 찾으면 ''."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, creationflags=NO_WINDOW, timeout=10)
        text = out.stdout.decode("mbcs" if IS_WIN else "utf-8", "replace")
        pid = None
        for line in text.splitlines():
            p = line.split()
            if len(p) >= 5 and p[0] == "TCP" and p[3] == "LISTENING" and p[1].rsplit(":", 1)[-1] == str(port):
                pid = p[4]
                break
        if not pid:
            return ""
        out = subprocess.run(["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
                             capture_output=True, creationflags=NO_WINDOW, timeout=10)
        line = out.stdout.decode("mbcs" if IS_WIN else "utf-8", "replace").strip().splitlines()
        name = line[0].split('","')[0].strip('"') if line and line[0].startswith('"') else "?"
        return f"PID {pid} ({name})"
    except Exception:
        return ""


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


# ----------------------------------------------------------------- 호스트 키 (known_hosts)
# 화면은 로그인 사용자의 %USERPROFILE%\.ssh\known_hosts(ssh.exe 로 접속해 본 키)도 믿고 쓰지만,
# 서비스(LocalSystem)는 그 파일을 볼 수 없다 → 화면에서 확인된 키는 반드시 프로그램 폴더 known_hosts 에도 적어 둔다.
USER_KNOWN_HOSTS = os.path.expanduser(os.path.join("~", ".ssh", "known_hosts"))


def host_key_name(host, port):
    return host if int(port) == 22 else f"[{host}]:{int(port)}"


def key_fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def sync_known_hosts(host, port, key=None):
    """프로그램 폴더 known_hosts 에 서버 키를 맞춘다. 바뀌었으면 (지문, 출처) 를, 아니면 None 을 돌려준다.

    key 를 주면(방금 접속에 성공해 확인된 키) 그 키로 맞추고, 없으면 사용자 known_hosts 에 있는 키를 옮긴다.
    """
    name = host_key_name(host, port)
    src = "접속 확인"
    if key is None:
        try:
            uk = paramiko.HostKeys(USER_KNOWN_HOSTS) if os.path.exists(USER_KNOWN_HOSTS) else None
        except Exception:
            uk = None
        found = uk.lookup(name) if uk else None
        if not found:
            return None
        key = next(iter(found.values()))
        src = USER_KNOWN_HOSTS
    try:
        hk = paramiko.HostKeys(KNOWN_HOSTS) if os.path.exists(KNOWN_HOSTS) else paramiko.HostKeys()
    except Exception:
        hk = paramiko.HostKeys()
    cur = hk.lookup(name)
    if cur and cur.get(key.get_name()) == key:
        return None
    if name in hk:
        del hk[name]          # 같은 서버의 예전 키(종류가 달라도)는 지운다
    hk.add(name, key.get_name(), key)
    hk.save(KNOWN_HOSTS)
    return key_fingerprint(key), src


# ----------------------------------------------------------------- SSH 엔진
class HostKeyRejected(Exception):
    pass


class ConnectionAttemptCancelled(Exception):
    """설정이 바뀌거나 중지된 뒤 도착한 연결 결과."""


class AskPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, engine):
        self.engine = engine
        self.generation = engine._generation

    def missing_host_key(self, client, hostname, key):
        fp = key_fingerprint(key)
        accepted = self.engine.ask_hostkey(hostname, key.get_name(), fp, generation=self.generation)
        with self.engine._lifecycle_lock:
            if self.generation != self.engine._generation:
                raise ConnectionAttemptCancelled()
            if not accepted:
                raise HostKeyRejected("호스트 키를 거부했습니다")
            client.get_host_keys().add(hostname, key.get_name(), key)
            client.save_host_keys(KNOWN_HOSTS)
        self.engine.log("INFO", f"호스트 키 등록: {hostname} {key.get_name()} {fp}")


class Engine:
    """경유 SSH 접속 1개를 유지(감시·재연결)하고 direct-tcpip 채널을 열어 준다."""

    BACKOFF = [3, 5, 10, 20, 30]

    def __init__(self, events: queue.Queue, sync_known=False):
        """sync_known=True(화면): 접속에 성공하면 확인된 서버 키를 프로그램 폴더 known_hosts 에 적는다(서비스용)."""
        self.events = events
        self.server = {}
        self.password = None
        self.client = None
        self.transport = None
        self.state = "끊김"
        self.want = False
        self.connected = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.last_ka_ok = 0.0
        self.ka_replied = False     # 이 연결에서 서버가 keepalive 에 한 번이라도 응답했는지
        self.ka_thread = None
        self.connected_at = None
        self.sync_known = sync_known
        self._lifecycle_lock = threading.RLock()
        self._generation = 0

    # -- GUI 로 보내기
    def log(self, level, msg):
        self.events.put(("log", level, msg))

    def ask_hostkey(self, host, ktype, fp, generation=None):
        generation = self._generation if generation is None else generation
        if generation != self._generation:
            return False
        done = threading.Event()
        box = {}
        self.events.put(("hostkey", host, ktype, fp, done, box))
        deadline = time.monotonic() + 300
        while not done.wait(0.1):
            if generation != self._generation or time.monotonic() >= deadline:
                return False
        return box.get("ok", False)

    def _set_state(self, s):
        if self.state != s:
            self.state = s
            self.events.put(("state", s))

    # -- 제어
    def start(self, server, password):
        with self._lifecycle_lock:
            self._generation += 1
            self._drop()
            self.server = dict(server)
            self.password = password
            self.want = True
            if not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._run, name="ssh-supervisor", daemon=True)
                self.thread.start()
            self.wake.set()

    def stop(self):
        with self._lifecycle_lock:
            self._generation += 1
            self.want = False
            self.wake.set()
            self._drop()
            self._set_state("끊김")

    def reconnect_now(self):
        with self._lifecycle_lock:
            self._generation += 1
            self._drop()
            self.wake.set()

    def _drop(self):
        with self._lifecycle_lock:
            self.connected.clear()
            c, self.client, self.transport = self.client, None, None
        if c:
            try:
                c.close()
            except Exception:
                pass

    def _connect(self):
        with self._lifecycle_lock:
            s = dict(self.server)
            password = self.password
            generation = self._generation
        c = paramiko.SSHClient()
        try:
            if os.path.exists(USER_KNOWN_HOSTS):
                c.load_system_host_keys(USER_KNOWN_HOSTS)
        except Exception:
            pass
        if not os.path.exists(KNOWN_HOSTS):
            open(KNOWN_HOSTS, "a").close()
        c.load_host_keys(KNOWN_HOSTS)
        c.set_missing_host_key_policy(AskPolicy(self))
        kw = dict(hostname=s["host"], port=int(s["port"]), username=s["user"], timeout=10,
                  banner_timeout=20, auth_timeout=30, allow_agent=False, look_for_keys=False)
        if s.get("auth") == "key":
            kw["key_filename"] = s.get("key_file") or None
            kw["passphrase"] = password or None
        else:
            kw["password"] = password or ""
        try:
            c.connect(**kw)
        except Exception:
            c.close()
            if generation != self._generation:
                raise ConnectionAttemptCancelled() from None
            raise
        t = c.get_transport()
        t.set_keepalive(0)
        self._tcp_keepalive(t)
        with self._lifecycle_lock:
            if generation != self._generation:
                c.close()
                raise ConnectionAttemptCancelled()
            if self.sync_known:
                try:
                    r = sync_known_hosts(s["host"], s["port"], t.get_remote_server_key())
                    if r:
                        self.log("INFO", f"서비스용 호스트 키 저장: {host_key_name(s['host'], s['port'])} {r[0]} → {KNOWN_HOSTS}")
                except Exception as e:
                    self.log("WARN", f"서비스용 호스트 키 저장 실패: {e}")
            self.client, self.transport = c, t
            self.last_ka_ok = time.time()
            self.ka_replied = False
            self._ka_note = False
            self.connected_at = time.time()
            self.connected.set()

    def _tcp_keepalive(self, t):
        """TCP keepalive — 서버가 SSH keepalive 에 응답하지 않는 경우에도 죽은 연결을 OS 가 찾아낸다."""
        try:
            sock = t.sock
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            iv = max(5, int(self.server.get("keepalive_interval", 30)))
            if IS_WIN:
                sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, iv * 1000, 5000))
        except Exception:
            pass

    def _keepalive_probe(self, t):
        try:
            t.global_request("keepalive@openssh.com", wait=True)
            with self._lifecycle_lock:
                if t is self.transport and t.is_active():
                    self.last_ka_ok = time.time()
                    self.ka_replied = True
        except Exception:
            pass

    def _run(self):
        fails = 0
        was_up = False
        last_probe = 0.0
        while True:
            if not self.want:
                self._drop()
                if self.state not in ("인증 실패", "호스트 키 불일치"):
                    self._set_state("끊김")
                self.wake.wait()
                self.wake.clear()
                fails, was_up = 0, False
                continue
            t = self.transport
            if t is None or not t.is_active():
                if was_up:
                    self.log("WARN", "SSH 연결이 끊어졌습니다. 자동으로 재연결합니다.")
                self._drop()
                self._set_state("재연결 중" if (was_up or fails) else "연결 중")
                s = self.server
                attempt_generation = self._generation
                try:
                    self.log("INFO", f"SSH 접속을 시도합니다: {s['user']}@{s['host']}:{s['port']}")
                    self._connect()
                    with self._lifecycle_lock:
                        if not self.want or self.transport is None:
                            continue
                        fails, was_up, last_probe = 0, True, time.time()
                        self._set_state("연결됨")
                        self.log("OK", f"SSH 연결이 완료되었습니다: {s['host']}:{s['port']} ({self.transport.remote_version})")
                except ConnectionAttemptCancelled:
                    continue
                except paramiko.AuthenticationException as e:
                    with self._lifecycle_lock:
                        if attempt_generation != self._generation:
                            continue
                        self.want = False
                        self._set_state("인증 실패")
                        self.log("ERROR", f"인증에 실패하였습니다: {e}")
                        self.events.put(("authfail", str(e)))
                    continue
                except paramiko.BadHostKeyException as e:
                    with self._lifecycle_lock:
                        if attempt_generation != self._generation:
                            continue
                        self.want = False
                        self._set_state("호스트 키 불일치")
                        self.log("ERROR", f"서버 호스트 키가 등록된 값과 다릅니다(서버 재설치 또는 중간자 공격 가능성). "
                                          f"변경 사실을 확인한 후 [도구 → 호스트 키 초기화]를 실행하십시오. ({e})")
                        self.events.put(("fatal", "호스트 키 불일치"))
                    continue
                except HostKeyRejected as e:
                    with self._lifecycle_lock:
                        if attempt_generation != self._generation:
                            continue
                        self.want = False
                        self._set_state("끊김")
                        self.log("ERROR", str(e))
                        self.events.put(("fatal", str(e)))
                    continue
                except Exception as e:
                    if attempt_generation != self._generation:
                        continue
                    wait = self.BACKOFF[min(fails, len(self.BACKOFF) - 1)]
                    fails += 1
                    self._set_state("재연결 대기")
                    self.log("ERROR", f"SSH 접속에 실패하였습니다({type(e).__name__}: {e}). {wait}초 후 다시 시도합니다({fails}회차).")
                    self.wake.wait(wait)
                    self.wake.clear()
                    continue
            else:
                iv = max(5, int(self.server.get("keepalive_interval", 30)))
                cnt = max(1, int(self.server.get("keepalive_count", 3)))
                now = time.time()
                if now - last_probe >= iv and not (self.ka_thread and self.ka_thread.is_alive()):
                    last_probe = now
                    self.ka_thread = threading.Thread(target=self._keepalive_probe, args=(t,), daemon=True)
                    self.ka_thread.start()
                if now - self.last_ka_ok > iv * cnt + 5:
                    if self.ka_replied:
                        self.log("WARN", f"서버 응답이 없습니다({iv}초 × {cnt}회). 연결을 종료하고 재연결합니다.")
                        self._drop()
                        continue
                    # 접속 후 한 번도 keepalive 응답이 없는 서버(일부 게이트웨이) — 끊지 않고 TCP keepalive 로 감시
                    if not getattr(self, "_ka_note", False):
                        self._ka_note = True
                        self.log("INFO", "서버가 SSH keepalive에 응답하지 않아 TCP keepalive로 연결 상태를 감시합니다.")
                    self.last_ka_ok = now
            self.wake.wait(1.0)
            self.wake.clear()

    def open_channel(self, host, port, src, wait=20):
        if not self.connected.wait(wait):
            return None, "SSH 서버에 연결되어 있지 않습니다(대기 시간 초과)."
        t = self.transport
        if t is None:
            return None, "SSH 서버에 연결되어 있지 않습니다."
        try:
            return t.open_channel("direct-tcpip", (host, int(port)), src, timeout=15), None
        except paramiko.ChannelException as e:
            return None, f"대상 연결 실패: {e.text} (코드 {e.code})"
        except Exception as e:
            return None, f"채널 열기 실패: {type(e).__name__}: {e}"


class PortInUse(Exception):
    pass


class Forwarder:
    """로컬 포트 1개를 listen 하고 들어온 접속을 SSH 채널로 중계."""

    def __init__(self, engine: Engine, tun: dict):
        self.engine = engine
        self.tun = dict(tun)
        self.sock = None
        self.stop_evt = threading.Event()
        self.lock = threading.Lock()
        self.conns = set()
        self.total = 0
        self.up = 0
        self.down = 0
        self.last_msg = ""
        self.last_err = False

    @property
    def label(self):
        return f"{self.tun['name']}({self.tun['local_port']})"

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if IS_WIN:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((self.tun.get("bind", "127.0.0.1"), int(self.tun["local_port"])))
        except OSError as e:
            s.close()
            raise PortInUse(str(e))
        s.listen(64)
        s.settimeout(1.0)
        self.sock = s
        threading.Thread(target=self._accept_loop, name=f"listen-{self.tun['local_port']}", daemon=True).start()

    def stop(self):
        self.stop_evt.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        with self.lock:
            items = list(self.conns)
        for c, ch in items:
            for x in (c, ch):
                try:
                    x.close()
                except Exception:
                    pass

    def _msg(self, text, err=False):
        self.last_msg = dt.datetime.now().strftime("%H:%M:%S ") + text
        self.last_err = err

    def _accept_loop(self):
        while not self.stop_evt.is_set():
            try:
                c, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            c.setblocking(True)
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._handle, args=(c, addr), daemon=True).start()

    def _handle(self, c, addr):
        if self.stop_evt.is_set():
            c.close()
            return
        chan, err = self.engine.open_channel(self.tun["remote_host"], self.tun["remote_port"], addr)
        if chan is None:
            self._msg(err, True)
            self.engine.log("ERROR", f"[{self.label}] {err}")
            c.close()
            return
        pair = (c, chan)
        with self.lock:
            if self.stop_evt.is_set():
                c.close()
                chan.close()
                return
            self.conns.add(pair)
            self.total += 1
        self._msg(f"접속 {addr[0]}:{addr[1]}")
        try:
            watch = [c, chan]
            while watch and not self.stop_evt.is_set():
                r, _, _ = select.select(watch, [], [], 1.0)
                if c in r:
                    d = c.recv(65536)
                    if d:
                        chan.sendall(d)
                        self.up += len(d)
                    else:
                        chan.shutdown_write()
                        watch.remove(c)
                if chan in r:
                    d = chan.recv(65536)
                    if d:
                        c.sendall(d)
                        self.down += len(d)
                    else:
                        break
        except Exception:
            pass
        finally:
            for x in (chan, c):
                try:
                    x.close()
                except Exception:
                    pass
            with self.lock:
                self.conns.discard(pair)

    @property
    def active(self):
        return len(self.conns)


# ----------------------------------------------------------------- 로그 파일
def prune_logs(days=30):
    cut = time.time() - days * 86400
    try:
        for n in os.listdir(LOG_DIR):
            p = os.path.join(LOG_DIR, n)
            if n.endswith(".log") and os.path.getmtime(p) < cut:
                os.remove(p)
    except OSError:
        pass


class FileLog:
    """logs\\<prefix>_YYYYMMDD.log 에 한 줄씩 남긴다(서비스 · 창 없는 실행용)."""

    def __init__(self, prefix, echo=False):
        self.prefix = prefix
        self.echo = echo
        self.lock = threading.Lock()
        prune_logs()

    def __call__(self, level, msg):
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [{level:5}] {msg}"
        with self.lock:
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                with open(os.path.join(LOG_DIR, f"{self.prefix}_{dt.date.today():%Y%m%d}.log"), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        if self.echo and sys.stdout:
            try:
                print(line, flush=True)
            except Exception:
                pass


# ----------------------------------------------------------------- 서비스 본체 (창 없이 터널 유지)
def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def _tun_key(t):
    return t.get("bind", "127.0.0.1"), int(t["local_port"]), t["remote_host"], int(t["remote_port"])


class Runner:
    """'자동(✓)' 터널을 창 없이 열고 유지 — Windows 서비스 · --headless 공용.

    - 설정 파일 · known_hosts 가 바뀌면 2초 안에 반영(바뀐 터널만 다시 열고, 서버 · 비밀번호가 바뀌면 SSH 재접속)
    - 포트가 막혀 있으면 30초마다 다시 시도(부팅 직후 다른 프로그램이 잠깐 잡는 경우)
    - 인증 실패는 10분 뒤 1회씩만 재시도(계정 잠금 방지), 모르는 호스트 키는 등록하지 않음(화면에서 먼저 등록)
    - 상태는 service_status.json 으로 화면에 알린다
    """
    AUTH_RETRY = 600
    PORT_RETRY = 30

    def __init__(self, log, mode="service"):
        self.log = log
        self.mode = mode
        self.events = queue.Queue()
        self.engine = Engine(self.events)
        self.fw = {}         # local_port -> Forwarder
        self.pending = {}    # local_port -> {"tun", "next", "msg"}
        self.stop_evt = threading.Event()
        self.cfg = None
        self.password = None
        self.server_sig = None
        self.stamp = None
        self.blocked = ""
        self.blocked_until = 0.0
        self.started = time.time()

    def stop(self):
        self.stop_evt.set()

    def _password(self, s):
        if s.get("password_svc"):
            try:
                return unprotect_password(s["password_svc"])
            except Exception:
                self.log("WARN", "서비스용 비밀번호를 복호화하지 못하였습니다(다른 PC에서 복사한 설정). 화면의 [비밀번호]로 다시 저장하십시오.")
        if s.get("password_dpapi"):
            try:   # 창 없는 실행을 사용자 계정으로 돌릴 때
                return unprotect_password(s["password_dpapi"])
            except Exception:
                pass
        return None

    def _reload(self):
        try:
            cfg = read_config()
        except Exception as e:
            self.log("ERROR", f"설정 파일을 읽지 못하여 이전 설정을 유지합니다: {e}")
            return
        s = cfg["server"]
        pw = self._password(s)
        sig = json.dumps([s.get(k) for k in ("host", "port", "user", "auth", "key_file",
                                             "keepalive_interval", "keepalive_count")]) + "|" + (pw or "")
        sig += "|" + str(_mtime(KNOWN_HOSTS))
        if sig != self.server_sig:
            if self.server_sig is not None:
                self.log("INFO", "경유 서버 · 비밀번호 · 호스트 키가 변경되어 SSH를 다시 연결합니다.")
            if self.engine.want:
                self.engine.stop()
            self.blocked, self.blocked_until = "", 0.0
            self.server_sig = sig
        self.cfg, self.password = cfg, pw
        want = {int(t["local_port"]): t for t in cfg["tunnels"] if t.get("auto")}
        for p, f in list(self.fw.items()):
            if p not in want or _tun_key(want[p]) != _tun_key(f.tun):
                f.stop()
                del self.fw[p]
                self.log("INFO", f"[{f.tun['name']}] 설정 변경으로 터널을 닫았습니다.")
            else:
                f.tun["name"] = want[p]["name"]
        old = self.pending
        self.pending = {}
        for p, t in want.items():
            if p not in self.fw:
                ent = old.get(p) or {"next": 0.0, "msg": ""}
                ent["tun"] = t
                self.pending[p] = ent
        if not want:
            self.log("WARN", "자동(✓) 터널이 없어 열 터널이 없습니다.")

    def _open_pending(self):
        now = time.time()
        for p, ent in list(self.pending.items()):
            if now < ent["next"]:
                continue
            t = ent["tun"]
            f = Forwarder(self.engine, t)
            try:
                f.start()
            except PortInUse:
                owner = port_owner(p)
                msg = f"포트 사용중 {owner}".strip()
                if ent["msg"] != msg:
                    self.log("ERROR", f"[{t['name']}] 내 PC 포트 {p}을(를) 다른 프로그램이 사용 중입니다 {owner}. "
                                      f"{self.PORT_RETRY}초마다 다시 시도합니다.")
                ent.update(msg=msg, next=now + self.PORT_RETRY)
                continue
            self.fw[p] = f
            del self.pending[p]
            self.log("OK", f"[{t['name']}] {t.get('bind', '127.0.0.1')}:{p} → {t['remote_host']}:{t['remote_port']} 터널을 열었습니다.")

    def _control(self):
        if not self.fw:
            if self.engine.want:
                self.engine.stop()
            return
        if self.engine.want:
            return
        if self.blocked:
            if self.blocked_until and time.time() >= self.blocked_until:
                self.log("INFO", f"대기 시간이 지나 다시 시도합니다({self.blocked}).")
                self.blocked, self.blocked_until = "", 0.0
            else:
                return
        s = self.cfg["server"]
        if s.get("auth") != "key" and self.password is None:
            self.blocked = "비밀번호 없음"
            self.log("ERROR", "저장된 서비스용 비밀번호가 없습니다. 화면의 [비밀번호]로 저장하십시오.")
            return
        self.engine.start(s, self.password)

    def _drain(self):
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return
            kind = ev[0]
            if kind == "log":
                self.log(ev[1], ev[2])
            elif kind == "hostkey":
                _, host, ktype, fp, done, box = ev
                box["ok"] = False
                done.set()
                self.log("ERROR", f"서버 호스트 키 {host} {ktype} {fp} 가 서비스용 known_hosts({KNOWN_HOSTS})에 없습니다. "
                                  "서비스는 호스트 키를 자동 등록하지 않습니다. 화면(CaptainTunnel.exe)을 실행하면 확인된 키가 자동으로 이전되며, "
                                  "이전할 키가 없으면 [비밀번호] 저장 시 지문을 확인한 후 등록됩니다.")
            elif kind == "authfail":
                self.blocked, self.blocked_until = "인증 실패", time.time() + self.AUTH_RETRY
                self.log("ERROR", f"인증에 실패하였습니다. {self.AUTH_RETRY // 60}분 후 다시 시도합니다(비밀번호를 다시 저장하면 즉시 반영됩니다).")
            elif kind == "fatal":
                self.blocked, self.blocked_until = ev[1], 0.0

    def _status(self, stopped=False):
        eng = self.engine
        up = eng.connected.is_set()
        rows = []
        if not stopped:
            for p, f in self.fw.items():
                rows.append({"local_port": p, "name": f.tun["name"], "status": "열림" if up else "열림(서버 대기)",
                             "err": f.last_err, "active": f.active, "total": f.total, "up": f.up, "down": f.down,
                             "msg": f.last_msg})
            for p, ent in self.pending.items():
                rows.append({"local_port": p, "name": ent["tun"]["name"], "status": "포트 사용중" if ent["msg"] else "여는 중",
                             "err": bool(ent["msg"]), "active": 0, "total": 0, "up": 0, "down": 0, "msg": ent["msg"]})
        s = (self.cfg or {}).get("server", {})
        st = {"mode": self.mode, "pid": os.getpid(), "version": VERSION, "started": self.started, "updated": time.time(),
              "stopped": stopped, "ssh_state": "중지" if stopped else (self.blocked or (eng.state if self.fw else "열 터널 없음")),
              "connected_since": eng.connected_at if up else None,
              "server": f"{s.get('user')}@{s.get('host')}:{s.get('port')}", "tunnels": rows}
        try:
            tmp = STATUS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(tmp, STATUS_PATH)
        except OSError:
            pass

    def run(self):
        self.log("INFO", f"{APP_NAME} {VERSION} {self.mode} 시작 · PID {os.getpid()} · 설정 {CONF_PATH}")
        last = 0.0
        try:
            while not self.stop_evt.is_set():
                now = time.time()
                if now - last >= 2:
                    last = now
                    stamp = (_mtime(CONF_PATH), _mtime(KNOWN_HOSTS), _mtime(INI_PATH))
                    if stamp != self.stamp:
                        if self.stamp is not None:
                            self.log("INFO", "설정 변경을 감지하여 반영합니다.")
                        self.stamp = stamp
                        self._reload()
                    if self.cfg:
                        self._open_pending()
                        self._control()
                    self._status()
                self._drain()
                self.stop_evt.wait(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            for f in self.fw.values():
                f.stop()
            self.fw.clear()
            self.engine.stop()
            self._drain()
            self._status(stopped=True)
            self.log("INFO", f"{self.mode} 실행을 종료합니다.")


def headless_main():
    log = FileLog("headless", echo=True)
    Runner(log, mode="headless").run()
    return 0


# ----------------------------------------------------------------- Windows 서비스
def service_dispatch():
    """SCM 이 '--service-run' 으로 실행. 터미널에서 직접 실행하면 1063 오류로 끝난다."""
    log = FileLog("service")
    try:
        import servicemanager
        import win32service
        import win32serviceutil
    except ImportError as e:
        log("ERROR", f"pywin32를 찾을 수 없습니다: {e}")
        return 1

    class TunnelService(win32serviceutil.ServiceFramework):
        _svc_name_ = SVC_NAME
        _svc_display_name_ = SVC_DISPLAY
        _svc_description_ = "캡틴 터널링 SSH 터널 유지"

        def __init__(self, args):
            super().__init__(args)
            self.runner = Runner(log, mode="service")

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.runner.stop()

        def SvcShutdown(self):
            self.SvcStop()

        def SvcDoRun(self):
            self.runner.run()

    try:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(TunnelService)
        servicemanager.StartServiceCtrlDispatcher()
    except Exception as e:
        log("ERROR", f"서비스 관리자에 연결하지 못하였습니다. 서비스로 설치된 상태에서만 실행됩니다({e}).")
        return 1
    return 0


SVC_STATES = {1: "중지됨", 2: "시작 중", 3: "중지 중", 4: "실행 중", 5: "계속 중", 6: "일시 중지 중", 7: "일시 중지"}
# 기본 서비스 권한 + 대화형 사용자(IU)에게 시작 · 중지 허용 → 설치 후 시작/중지는 관리자 승인 없이
SVC_SDDL = ("D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWRPWPDTLOCRRC;;;IU)"
            "(A;;CCLCSWLOCRRC;;;SU)S:(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)")


class _SSP(ctypes.Structure):
    _fields_ = [(n, wintypes.DWORD) for n in ("type", "state", "accepted", "exit", "svc_exit",
                                               "checkpoint", "wait", "pid", "flags")]


class _QSC(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("start", wintypes.DWORD), ("errctl", wintypes.DWORD),
                ("binpath", wintypes.LPWSTR), ("group", wintypes.LPWSTR), ("tag", wintypes.DWORD),
                ("deps", wintypes.LPWSTR), ("account", wintypes.LPWSTR), ("display", wintypes.LPWSTR)]


def _advapi():
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    H, D = wintypes.HANDLE, wintypes.DWORD
    a.OpenSCManagerW.restype = H
    a.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, D]
    a.OpenServiceW.restype = H
    a.OpenServiceW.argtypes = [H, wintypes.LPCWSTR, D]
    a.CloseServiceHandle.argtypes = [H]
    a.QueryServiceStatusEx.argtypes = [H, ctypes.c_int, ctypes.c_void_p, D, ctypes.POINTER(D)]
    a.QueryServiceConfigW.argtypes = [H, ctypes.c_void_p, D, ctypes.POINTER(D)]
    a.StartServiceW.argtypes = [H, D, ctypes.c_void_p]
    a.ControlService.argtypes = [H, D, ctypes.c_void_p]
    return a


def svc_query():
    """(상태, 정보) — 상태 None=설치 안 됨 · -1=확인 불가 · 1..7=SERVICE_* (4 실행 중)."""
    if not IS_WIN:
        return -1, {}
    a = _advapi()
    scm = a.OpenSCManagerW(None, None, 0x0001)
    if not scm:
        return -1, {"error": ctypes.get_last_error()}
    try:
        h = a.OpenServiceW(scm, SVC_NAME, 0x0004 | 0x0001)       # QUERY_STATUS | QUERY_CONFIG
        if not h:
            e = ctypes.get_last_error()
            return (None if e == 1060 else -1), {"error": e}
        try:
            ssp, need = _SSP(), wintypes.DWORD()
            if not a.QueryServiceStatusEx(h, 0, ctypes.byref(ssp), ctypes.sizeof(ssp), ctypes.byref(need)):
                return -1, {"error": ctypes.get_last_error()}
            info = {"pid": ssp.pid, "exit": ssp.exit}
            a.QueryServiceConfigW(h, None, 0, ctypes.byref(need))
            buf = ctypes.create_string_buffer(max(need.value, ctypes.sizeof(_QSC)))
            if a.QueryServiceConfigW(h, buf, len(buf), ctypes.byref(need)):
                q = ctypes.cast(buf, ctypes.POINTER(_QSC)).contents
                info.update(binpath=q.binpath or "", start=q.start)
            return ssp.state, info
        finally:
            a.CloseServiceHandle(h)
    finally:
        a.CloseServiceHandle(scm)


def svc_control(action):
    """관리자 승인 없이 시작/중지(설치 때 권한을 준 경우). 반환 0=성공, 5=권한 없음, 그 밖 Win32 오류."""
    a = _advapi()
    scm = a.OpenSCManagerW(None, None, 0x0001)
    if not scm:
        return ctypes.get_last_error()
    try:
        h = a.OpenServiceW(scm, SVC_NAME, 0x0010 if action == "start" else 0x0020)
        if not h:
            return ctypes.get_last_error()
        try:
            if action == "start":
                ok = a.StartServiceW(h, 0, None)
            else:
                st = (wintypes.DWORD * 7)()
                ok = a.ControlService(h, 1, ctypes.byref(st))     # SERVICE_CONTROL_STOP
            if ok:
                return 0
            e = ctypes.get_last_error()
            return 0 if e in (1056, 1062) else e                  # 이미 실행 중 · 이미 중지
        finally:
            a.CloseServiceHandle(h)
    finally:
        a.CloseServiceHandle(scm)


def self_command(args):
    """이 프로그램을 인자와 함께 다시 실행하는 (exe, 인자) — 개발 실행이면 python + 스크립트."""
    if FROZEN:
        return sys.executable, args
    return sys.executable, f'"{source_launcher()}" {args}'


class _SEI(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def run_elevated(args, timeout=180):
    """관리자 승인(UAC)으로 이 프로그램을 실행하고 끝날 때까지 기다린다. (종료 코드 | None, Win32 오류)"""
    exe, params = self_command(args)
    sei = _SEI()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = 0x40 | 0x400                 # NOCLOSEPROCESS | FLAG_NO_UI
    sei.lpVerb, sei.lpFile, sei.lpParameters, sei.lpDirectory, sei.nShow = "runas", exe, params, EXE_DIR, 0
    sh = ctypes.WinDLL("shell32", use_last_error=True)
    sh.ShellExecuteExW.argtypes = [ctypes.POINTER(_SEI)]
    if not sh.ShellExecuteExW(ctypes.byref(sei)):
        return None, ctypes.get_last_error()   # 1223 = 사용자가 취소
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.WaitForSingleObject(sei.hProcess, timeout * 1000)
    code = wintypes.DWORD()
    k.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    k.CloseHandle(sei.hProcess)
    return code.value, 0


def _sc(log, *args):
    r = subprocess.run(["sc.exe", *args], capture_output=True, creationflags=NO_WINDOW, timeout=60)
    out = " ".join(r.stdout.decode("mbcs", "replace").split())
    log("INFO" if r.returncode == 0 else "WARN", f"sc {args[0]} → {r.returncode} {out[:300]}")
    return r.returncode


def _svc_wait(state, sec=30):
    end = time.time() + sec
    while time.time() < end:
        if svc_query()[0] in (state, None):
            return True
        time.sleep(0.5)
    return False


def service_admin(action):
    """관리자 권한으로 실행되는 서비스 설치 · 제거 · 시작 · 중지. 종료 코드 0=성공."""
    log = FileLog("service_admin")
    if action == "install" and not FROZEN:
        log("ERROR", "서비스 설치는 빌드된 CaptainTunnel.exe에서 실행하십시오.")
        return 2
    state, _ = svc_query()
    log("INFO", f"서비스 {action} 요청 · 현재 {SVC_STATES.get(state, '설치 안 됨' if state is None else '확인 불가')} · {sys.executable}")
    try:
        if action == "install":
            if FROZEN:
                binpath = f'"{sys.executable}" --service-run'
            else:
                binpath = f'"{sys.executable}" "{source_launcher()}" --service-run'
            if state is None:
                rc = _sc(log, "create", SVC_NAME, "binPath=", binpath, "start=", "auto", "DisplayName=", SVC_DISPLAY)
            else:
                if state != 1:
                    _sc(log, "stop", SVC_NAME)
                    _svc_wait(1)
                rc = _sc(log, "config", SVC_NAME, "binPath=", binpath, "start=", "auto", "DisplayName=", SVC_DISPLAY)
            if rc:
                return rc
            _sc(log, "description", SVC_NAME, f"캡틴 터널링 SSH 터널을 PC 시작 시 자동으로 열고 유지합니다. 설정: {CONF_PATH}")
            _sc(log, "failure", SVC_NAME, "reset=", "86400", "actions=", "restart/10000/restart/30000/restart/60000")
            _sc(log, "failureflag", SVC_NAME, "1")
            _sc(log, "sdset", SVC_NAME, SVC_SDDL)
            rc = _sc(log, "start", SVC_NAME)
            return 0 if rc in (0, 1056) else rc
        if action == "uninstall":
            if state is None:
                return 0
            if state != 1:
                _sc(log, "stop", SVC_NAME)
                _svc_wait(1)
            rc = _sc(log, "delete", SVC_NAME)
            if rc == 0:
                try:
                    os.remove(STATUS_PATH)
                except OSError:
                    pass
            return rc
        if action in ("start", "stop"):
            rc = _sc(log, action, SVC_NAME)
            return 0 if rc in (0, 1056, 1062) else rc
        log("ERROR", f"알 수 없는 작업: {action}")
        return 2
    except Exception as e:
        log("ERROR", f"{type(e).__name__}: {e}")
        return 3


def login_autostart(enable=None):
    """로그인 시 화면 자동 실행(HKCU Run, 관리자 권한 불필요). enable=None 이면 등록 명령(없으면 '')."""
    import winreg
    if FROZEN:
        cmd = f'"{sys.executable}" --autostart --minimized'
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = f'"{pyw if os.path.exists(pyw) else sys.executable}" "{source_launcher()}" --autostart --minimized'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        if enable is None:
            try:
                return winreg.QueryValueEx(k, RUN_VALUE)[0]
            except FileNotFoundError:
                return ""
        if enable:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, RUN_VALUE)
            except FileNotFoundError:
                pass
        return cmd if enable else ""


# ----------------------------------------------------------------- GUI
class Icons:
    """내장 PNG 아이콘(icons_data) — 고DPI 배율에 가장 가까운 크기를 쓴다. 없으면 "" (글자만)."""
    FALLBACK = {"up": "▲", "down": "▼", "folder": "…"}

    def __init__(self, scale):
        sizes = sorted(icons_data.ICONS) if icons_data else []
        self.size = min(sizes, key=lambda n: abs(n - 16 * scale)) if sizes else None
        self.cache = {}

    def get(self, name):
        if name not in self.cache:
            img = ""
            if self.size and name in icons_data.ICONS[self.size]:
                try:
                    img = tk.PhotoImage(data=icons_data.ICONS[self.size][name])
                except tk.TclError:
                    img = ""
            self.cache[name] = img
        return self.cache[name]


class Tip:
    """마우스를 올리면 잠시 뒤 설명을 띄운다."""

    def __init__(self, w, text):
        self.w, self.text, self.tw, self.job = w, text, None, None
        w.bind("<Enter>", self._enter, add="+")
        w.bind("<Leave>", self._leave, add="+")
        w.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _e):
        self.job = self.w.after(450, self._show)

    def _show(self):
        self.job = None
        x = self.w.winfo_rootx() + 6
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        self.tw = tk.Toplevel(self.w)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tw, text=self.text, background="#ffffe1", foreground="#24292f", relief="solid", borderwidth=1,
                 justify="left", padx=7, pady=4, font=("Malgun Gothic", 9)).pack()

    def _leave(self, _e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tw:
            self.tw.destroy()
            self.tw = None


class TunnelDialog(tk.Toplevel):
    def __init__(self, master, title, tun=None):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        tun = tun or {"name": "", "local_port": "", "remote_host": "", "remote_port": "", "bind": "127.0.0.1", "auto": True}
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        self.v = {
            "name": tk.StringVar(value=tun["name"]),
            "local_port": tk.StringVar(value=str(tun["local_port"])),
            "remote_host": tk.StringVar(value=tun["remote_host"]),
            "remote_port": tk.StringVar(value=str(tun["remote_port"])),
            "bind": tk.StringVar(value=tun.get("bind", "127.0.0.1")),
            "auto": tk.BooleanVar(value=tun.get("auto", True)),
        }
        rows = [("이름", "name", 34), ("내 PC 포트", "local_port", 10),
                ("원격 대상 주소", "remote_host", 24), ("원격 대상 포트", "remote_port", 10)]
        for i, (lab, key, w) in enumerate(rows):
            ttk.Label(f, text=lab).grid(row=i, column=0, sticky="w", pady=4, padx=(0, 10))
            e = ttk.Entry(f, textvariable=self.v[key], width=w)
            e.grid(row=i, column=1, sticky="w", pady=4)
            if i == 0:
                e.focus_set()
        ttk.Label(f, text="열 주소").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v["bind"], values=["127.0.0.1", "0.0.0.0"], width=12,
                     state="readonly").grid(row=4, column=1, sticky="w")
        ttk.Checkbutton(f, text="자동 시작 대상 (실행 시 자동 열기)", variable=self.v["auto"]).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(f, text="※ 원격 대상 127.0.0.1 은 내 PC 가 아니라 경유 SSH 서버 자신입니다.\n"
                          "※ 열 주소 0.0.0.0 은 다른 PC 도 내 PC 를 통해 접속할 수 있게 되므로 권장하지 않습니다.",
                  foreground="#666").grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        b = ttk.Frame(f)
        b.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(b, text="확인", command=self._ok).pack(side="left", padx=4)
        ttk.Button(b, text="취소", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.wait_window()

    def _ok(self):
        try:
            name = self.v["name"].get().strip()
            lp = int(self.v["local_port"].get())
            rh = self.v["remote_host"].get().strip()
            rp = int(self.v["remote_port"].get())
            if not name or not rh or not (1 <= lp <= 65535) or not (1 <= rp <= 65535):
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "이름과 주소를 입력하고, 포트는 1~65535 사이의 숫자로 입력하십시오.", parent=self)
            return
        self.result = {"name": name, "local_port": lp, "remote_host": rh, "remote_port": rp,
                       "bind": self.v["bind"].get(), "auto": self.v["auto"].get()}
        self.destroy()


class PasswordDialog(tk.Toplevel):
    def __init__(self, master, prompt, remember, show_remember=True, title="인증"):
        super().__init__(master)
        self.title(APP_NAME + " — " + title)
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        f = ttk.Frame(self, padding=14)
        f.pack()
        ttk.Label(f, text=prompt).pack(anchor="w")
        self.pw = tk.StringVar()
        e = ttk.Entry(f, textvariable=self.pw, show="●", width=34)
        e.pack(anchor="w", pady=8)
        e.focus_set()
        self.remember = tk.BooleanVar(value=remember)
        if show_remember:
            ttk.Checkbutton(f, text="비밀번호 기억 (이 PC · 현재 Windows 사용자만 복호화)", variable=self.remember).pack(anchor="w")
        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(12, 0))
        ttk.Button(b, text="연결", command=self._ok).pack(side="left", padx=4)
        ttk.Button(b, text="취소", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.lift()
        self.wait_window()

    def _ok(self):
        self.result = (self.pw.get(), self.remember.get())
        self.destroy()


STATE_COLOR = {"연결됨": "#1a7f37", "연결 중": "#9a6700", "재연결 중": "#9a6700", "재연결 대기": "#9a6700",
               "끊김": "#666666", "인증 실패": "#cf222e", "호스트 키 불일치": "#cf222e"}


class App:
    COLS = [("auto", "자동", 46, "center"), ("name", "이름", 380, "w"), ("local", "내 PC 주소", 130, "w"),
            ("remote", "원격 대상", 160, "w"), ("state", "상태", 120, "w"), ("conns", "연결", 70, "center"),
            ("traffic", "송신 / 수신", 130, "center"), ("msg", "최근 메시지", 320, "w")]

    def __init__(self, root, autostart=False):
        self.root = root
        self.events = queue.Queue()
        self.engine = Engine(self.events, sync_known=True)
        self.cfg = load_config()
        self.fw = {}            # index -> Forwarder
        self.port_err = {}      # index -> message
        self.svc_state, self.svc_info, self.svc_st = -1, {}, None   # 서비스 상태 · 정보 · service_status.json
        self.svc_busy = False
        self._build()
        self._load_server_fields()
        self._svc_poll()
        self._refresh_tree(full=True)
        prune_logs()
        self.log("INFO", f"{APP_NAME} {VERSION} 시작 · 설정 {CONF_PATH}")
        if self.cfg["hidden_tunnels"]:
            self.log("INFO", f"숨긴 터널 {self._hidden_summary()} — config.ini 설정 "
                             f"{', '.join(f'show_{k} = ' + str(v).lower() for k, v in self.cfg['show'].items())}. "
                             f"표시하려면 해당 값을 true 로 변경한 후 다시 실행하십시오(파일 → config.ini 열기).")
        if self.svc_mode:
            self.log("INFO", "Windows 서비스가 터널을 유지하고 있습니다. 이 화면은 상태 확인 및 설정 관리에 사용합니다.")
        self._sync_svc_hostkey()
        self.root.after(200, self._pump)
        self.root.after(1000, self._tick)
        if (autostart or self.cfg.get("autostart_on_launch")) and not self.svc_mode:
            self.root.after(500, lambda: self.start_tunnels([i for i, t in enumerate(self.cfg["tunnels"]) if t.get("auto")]))

    @property
    def svc_mode(self):
        """서비스가 실행(또는 시작) 중이면 터널은 서비스가 연다 — 화면에서 같은 포트를 열지 않는다."""
        return self.svc_state in (2, 4)

    # ---------------- 화면 구성
    def _btn(self, parent, icon, text, cmd, tip=None):
        img = self.ic.get(icon)
        label = (" " + text) if text else ("" if img else Icons.FALLBACK.get(icon, icon))
        b = ttk.Button(parent, text=label, image=img, compound="left" if text else "image" if img else "none",
                       command=cmd, width=0, padding=(8, 2) if text else (3, 2))   # 글자 길이만큼 — 간결한 버튼
        if tip:
            Tip(b, tip)
        return b

    def _title(self, parent, icon, text):
        return ttk.Label(parent, text=" " + text, image=self.ic.get(icon), compound="left",
                         font=("Malgun Gothic", 10, "bold"))

    def _menu(self, parent, items):
        m = tk.Menu(parent, tearoff=0)
        for it_ in items:
            if it_ is None:
                m.add_separator()
            else:
                icon, label, cmd = it_
                m.add_command(label="  " + label, image=self.ic.get(icon), compound="left", command=cmd)
        return m

    def _build(self):
        r = self.root
        r.title(f"{APP_NAME} — SSH 터널 관리 ({VERSION})")
        r.minsize(int(980 * SCALE), int(560 * SCALE))
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        self.ic = Icons(SCALE)

        mb = tk.Menu(r)
        mb.add_cascade(label="파일", menu=self._menu(mb, [
            ("folder", "설정 폴더 열기", lambda: os.startfile(APP_DIR)),
            ("setting", "config.ini 열기 (MIG · 개발망 · 공사망 표시 설정)", self.open_ini),
            ("log", "로그 폴더 열기", self._open_logs), None,
            ("exit", "종료", self.on_close)]))
        mb.add_cascade(label="도구", menu=self._menu(mb, [
            ("diag", "포트 점검", self.check_ports),
            ("test", "대상 연결 시험 (선택 터널)", self.test_targets),
            ("refresh", "SSH 서버 재연결", self.reconnect), None,
            ("sync", "표준 터널 목록 반영 (이름 정리 · DB 추가)", self.apply_std_tunnels),
            ("sort", "이름순 정렬 (오름차순 ↔ 내림차순)", self.sort_by_name), None,
            ("key", "저장된 비밀번호 삭제", self.forget_password),
            ("lock", "호스트 키 초기화", self.reset_hostkey), None,
            ("copy", "ssh.exe 명령 복사 (대체 실행용)", self.copy_ssh_cmd)]))
        mb.add_cascade(label="서비스", menu=self._menu(mb, [
            ("install", "서비스 설치 · 갱신 (관리자 승인)", self.svc_install),
            ("start", "서비스 시작", lambda: self.svc_start_stop("start")),
            ("stop", "서비스 중지", lambda: self.svc_start_stop("stop")),
            ("key", "서비스 비밀번호 저장 · 변경", self.svc_password),
            ("log", "서비스 로그 보기", self.svc_open_log), None,
            ("delete", "서비스 제거 (관리자 승인)", self.svc_uninstall)]))
        mb.add_cascade(label="도움말", menu=self._menu(mb, [("help", "사용 방법", self.show_help)]))
        r.config(menu=mb)

        # 경유 서버
        sv = ttk.LabelFrame(r, padding=(10, 6))
        sv.configure(labelwidget=self._title(sv, "server", "경유 SSH 서버"))
        sv.pack(fill="x", padx=10, pady=(8, 4))
        self.sv = {k: tk.StringVar() for k in ("host", "port", "user", "auth", "key_file")}
        ttk.Label(sv, text="주소").grid(row=0, column=0, sticky="w")
        ttk.Entry(sv, textvariable=self.sv["host"], width=18).grid(row=0, column=1, padx=(4, 12))
        ttk.Label(sv, text="포트").grid(row=0, column=2, sticky="w")
        ttk.Entry(sv, textvariable=self.sv["port"], width=7).grid(row=0, column=3, padx=(4, 12))
        ttk.Label(sv, text="계정").grid(row=0, column=4, sticky="w")
        ttk.Entry(sv, textvariable=self.sv["user"], width=12).grid(row=0, column=5, padx=(4, 12))
        ttk.Label(sv, text="인증").grid(row=0, column=6, sticky="w")
        cb = ttk.Combobox(sv, textvariable=self.sv["auth"], values=["비밀번호", "키 파일"], width=8, state="readonly")
        cb.grid(row=0, column=7, padx=(4, 6))
        cb.bind("<<ComboboxSelected>>", lambda e: self._auth_changed())
        self.key_entry = ttk.Entry(sv, textvariable=self.sv["key_file"], width=22)
        self.key_entry.grid(row=0, column=8)
        self.key_btn = self._btn(sv, "folder", "", self._pick_key, "개인 키 파일을 선택합니다.")
        self.key_btn.grid(row=0, column=9, padx=(2, 12))
        self.dot = tk.Label(sv, text="●", font=("Malgun Gothic", 14), fg="#666666")
        self.dot.grid(row=0, column=10, padx=(6, 2))
        self.state_lbl = ttk.Label(sv, text="끊김", width=14)
        self.state_lbl.grid(row=0, column=11, sticky="w")
        self.conn_btn = self._btn(sv, "connect", "연결", self.toggle_connect,
                                  "경유 SSH 서버에 연결하거나 연결을 해제합니다.")
        self.conn_btn.grid(row=0, column=12)
        sv.columnconfigure(11, weight=1)

        # 윈도우 서비스
        sf = ttk.LabelFrame(r, padding=(10, 6))
        sf.configure(labelwidget=self._title(sf, "shield", "Windows 서비스 — PC 시작 시 자동(✓) 터널 유지"))
        sf.pack(fill="x", padx=10, pady=(2, 4))
        self.svc_dot = tk.Label(sf, text="●", font=("Malgun Gothic", 14), fg="#666666")
        self.svc_dot.grid(row=0, column=0, padx=(0, 4))
        self.svc_lbl = ttk.Label(sf, text="확인 중", anchor="w")
        self.svc_lbl.grid(row=0, column=1, sticky="we")
        sf.columnconfigure(1, weight=1)
        row = ttk.Frame(sf)
        row.grid(row=1, column=1, sticky="we", pady=(4, 0))
        self.svc_btns = {}
        for key, icon, text, cmd, tip in [
                ("install", "install", "설치", self.svc_install,
                 "Windows 서비스로 설치하거나 현재 실행 파일로 갱신합니다(관리자 승인 필요)."),
                ("start", "start", "시작", lambda: self.svc_start_stop("start"), "서비스를 시작합니다."),
                ("stop", "stop", "중지", lambda: self.svc_start_stop("stop"), "서비스를 중지합니다. 서비스가 연 터널이 닫힙니다."),
                ("password", "key", "비밀번호", self.svc_password,
                 "서비스용 SSH 비밀번호를 저장하거나 변경합니다(로그인 확인 후 저장)."),
                ("log", "log", "로그", self.svc_open_log, "서비스 로그 파일을 엽니다."),
                ("uninstall", "delete", "제거", self.svc_uninstall, "서비스를 제거합니다(관리자 승인 필요).")]:
            b = self._btn(row, icon, text, cmd, tip)
            b.pack(side="left", padx=(0, 4))
            self.svc_btns[key] = b
        self.login_var = tk.BooleanVar(value=False)
        try:
            self.login_var.set(bool(login_autostart()))
        except OSError:
            pass
        cb = ttk.Checkbutton(row, text="로그인 시 화면 자동 실행", variable=self.login_var, command=self._login_changed)
        cb.pack(side="right")
        Tip(cb, "서비스를 사용할 수 없는 경우에 사용합니다(관리자 권한 불필요).\n"
                "Windows 로그인 후 이 화면이 실행되어 자동(✓) 터널을 엽니다.")
        self.svc_note = ttk.Label(sf, text="", foreground="#9a6700")
        self.svc_note.grid(row=2, column=1, sticky="w")

        # 버튼 줄
        bar = ttk.Frame(r)
        bar.pack(fill="x", padx=10, pady=(6, 2))
        for icon, text, cmd, tip in [
                ("start", "시작", self.start_selected, "선택한 터널을 엽니다(행 더블클릭과 같습니다)."),
                ("stop", "중지", self.stop_selected, "선택한 터널을 닫습니다."),
                ("start_all", "전체 시작", self.start_all, "목록의 모든 터널을 엽니다."),
                ("stop_all", "전체 중지", self.stop_all, "열린 터널을 모두 닫습니다.")]:
            self._btn(bar, icon, text, cmd, tip).pack(side="left", padx=(0, 4))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        for icon, text, cmd, tip in [
                ("add", "추가", self.add_tunnel, "새 터널을 추가합니다."),
                ("edit", "편집", self.edit_tunnel, "선택한 터널을 편집합니다(열린 터널은 먼저 중지)."),
                ("delete", "삭제", self.del_tunnel, "선택한 터널을 삭제합니다."),
                ("up", "", lambda: self.move(-1), "선택한 터널을 위로 이동합니다."),
                ("down", "", lambda: self.move(1), "선택한 터널을 아래로 이동합니다.")]:
            self._btn(bar, icon, text, cmd, tip).pack(side="left", padx=(0, 4))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self._btn(bar, "diag", "포트 점검", self.check_ports,
                  "내 PC 포트가 비어 있는지, 사용 중이면 어떤 프로그램인지 확인합니다.").pack(side="left", padx=(0, 4))
        self._btn(bar, "test", "연결 시험", self.test_targets,
                  "SSH 서버에서 원격 대상까지 연결되는지 시험합니다(SSH 연결 상태에서).").pack(side="left")
        self.auto_var = tk.BooleanVar(value=self.cfg.get("autostart_on_launch", False))
        cb = ttk.Checkbutton(bar, text="실행 시 자동 터널 열기", variable=self.auto_var, command=self._auto_changed)
        cb.pack(side="right")
        Tip(cb, "다음 실행부터 자동(✓) 터널을 바로 엽니다.")

        # 터널 표 — 맨 앞 칸은 상태 점(초록 열림 · 노랑 대기 · 빨강 오류 · 회색 중지)
        pw = ttk.PanedWindow(r, orient="vertical")
        pw.pack(fill="both", expand=True, padx=10, pady=(4, 8))
        tf = ttk.Frame(pw)
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.COLS], show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=int(34 * SCALE), minwidth=int(34 * SCALE), stretch=False, anchor="center")
        for key, text, w, anc in self.COLS:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=int(w * SCALE), anchor=anc, stretch=(key == "msg"))
        self.sort_desc = None       # 이름 정렬 상태 — None(사용자 순서) · False(오름) · True(내림)
        self.tree.heading("name", command=self.sort_by_name)
        self._sort_mark()
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.tag_configure("open", background="#dafbe1")
        self.tree.tag_configure("wait", background="#fff8c5")
        self.tree.tag_configure("err", background="#ffebe9")
        self.tree.tag_configure("off", background="#ffffff")
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Button-3>", self._on_right)
        self.tree.bind("<Delete>", lambda e: self.del_tunnel())
        pw.add(tf, weight=3)

        self.ctx = self._menu(r, [
            ("start", "시작", self.start_selected),
            ("stop", "중지", self.stop_selected), None,
            ("copy", "내 PC 주소 복사 (127.0.0.1:포트)", self.copy_addr),
            ("test", "대상 연결 시험", self.test_targets),
            ("edit", "편집", self.edit_tunnel),
            ("delete", "삭제", self.del_tunnel)])

        # 로그
        lf = ttk.LabelFrame(pw, padding=4)
        lf.configure(labelwidget=self._title(lf, "log", "로그"))
        self.logbox = tk.Text(lf, height=9, wrap="word", font=("Consolas", 9), state="disabled",
                              background="#fbfbfb", relief="flat")
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=ls.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        ls.pack(side="right", fill="y")
        for lv, col in (("INFO", "#333333"), ("OK", "#1a7f37"), ("WARN", "#9a6700"), ("ERROR", "#cf222e")):
            self.logbox.tag_configure(lv, foreground=col)
        pw.add(lf, weight=1)

        self.status = ttk.Label(r, text="", anchor="w", padding=(10, 2))
        self.status.pack(fill="x", side="bottom")

    def _load_server_fields(self):
        s = self.cfg["server"]
        self.sv["host"].set(s["host"])
        self.sv["port"].set(str(s["port"]))
        self.sv["user"].set(s["user"])
        self.sv["auth"].set("키 파일" if s.get("auth") == "key" else "비밀번호")
        self.sv["key_file"].set(s.get("key_file", ""))
        self._auth_changed()

    def _auth_changed(self):
        st = "normal" if self.sv["auth"].get() == "키 파일" else "disabled"
        self.key_entry.configure(state=st)
        self.key_btn.configure(state=st)

    def _pick_key(self):
        p = filedialog.askopenfilename(title="개인 키 파일", initialdir=os.path.expanduser("~/.ssh"))
        if p:
            self.sv["key_file"].set(p)

    def _auto_changed(self):
        self.cfg["autostart_on_launch"] = self.auto_var.get()
        self._save()

    def _server_from_fields(self):
        s = self.cfg["server"]
        try:
            port = int(self.sv["port"].get())
        except ValueError:
            raise ValueError("SSH 서버 포트는 숫자로 입력하십시오.")
        host, user = self.sv["host"].get().strip(), self.sv["user"].get().strip()
        if not host or not user:
            raise ValueError("SSH 서버 주소와 계정을 입력하십시오.")
        changed = (host, port, user) != (s["host"], int(s["port"]), s["user"])
        s.update(host=host, port=port, user=user, key_file=self.sv["key_file"].get().strip(),
                 auth="key" if self.sv["auth"].get() == "키 파일" else "password")
        if changed and (s.get("password_dpapi") or s.get("password_svc")):
            s["password_dpapi"] = ""
            s["password_svc"] = ""
            s["remember_password"] = False
            self.engine.password = None
            self.log("INFO", "서버 또는 계정이 변경되어 저장된 비밀번호(화면용 · 서비스용)를 삭제하였습니다."
                             + (" 서비스용 비밀번호는 [비밀번호]로 다시 저장하십시오." if self.svc_state not in (None, -1) else ""))
        self._save()
        return s

    def _save(self):
        try:
            save_config(self.cfg)
        except OSError as e:
            self.log("ERROR", f"설정을 저장하지 못하였습니다: {e}")

    # ---------------- 로그
    def log(self, level, msg):
        ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} [{level:5}] {msg}"
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line + "\n", level)
        if int(self.logbox.index("end-1c").split(".")[0]) > 3000:
            self.logbox.delete("1.0", "500.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(os.path.join(LOG_DIR, f"captain_tunnel_{dt.date.today():%Y%m%d}.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _open_logs(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        os.startfile(LOG_DIR)

    # ---------------- 엔진 이벤트
    def _pump(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.log(ev[1], ev[2])
                elif kind == "state":
                    self._show_state(ev[1])
                elif kind == "hostkey":
                    _, host, ktype, fp, done, box = ev
                    box["ok"] = messagebox.askyesno(
                        APP_NAME + " — 처음 접속하는 서버",
                        f"처음 접속하는 서버입니다. 호스트 키를 확인하십시오.\n\n서버: {host}\n종류: {ktype}\n지문: {fp}\n\n"
                        "이 서버를 신뢰하고 등록하시겠습니까?\n(ssh.exe 의 'Are you sure you want to continue connecting' 과 같습니다)",
                        parent=self.root)
                    done.set()
                elif kind == "authfail":
                    self._on_authfail()
                elif kind == "fatal":
                    self.status.configure(text=f"연결이 중단되었습니다: {ev[1]}")
                elif kind == "call":        # 작업 스레드 결과를 화면 스레드에서 처리
                    ev[1]()
        except queue.Empty:
            pass
        self.root.after(200, self._pump)

    def _show_state(self, s):
        self.dot.configure(fg=STATE_COLOR.get(s, "#666666"))
        self.state_lbl.configure(text=s)
        want = self.engine.want
        self.conn_btn.configure(text=" 연결 해제" if want else " 연결", image=self.ic.get("disconnect" if want else "connect"))

    def _on_authfail(self):
        s = self.cfg["server"]
        if s.get("password_dpapi"):
            s["password_dpapi"] = ""
            s["remember_password"] = False
            self._save()
            self.log("WARN", "저장된 비밀번호가 일치하지 않아 삭제하였습니다.")
        self.engine.password = None
        if not self.fw:
            return
        pw = self._ask_password("비밀번호가 일치하지 않습니다. 다시 입력하십시오.")
        if pw is None:
            self.log("INFO", "인증이 취소되어 열린 터널을 모두 닫습니다.")
            self.stop_all()
        else:
            self.engine.start(s, pw)

    # ---------------- 연결 제어
    def _ask_password(self, prompt=None):
        s = self.cfg["server"]
        if s.get("auth") == "key":
            prompt = prompt or f"키 파일 암호(passphrase)를 입력하십시오. 암호가 없으면 비워 두십시오.\n{s.get('key_file')}"
        else:
            prompt = prompt or f"{s['user']}@{s['host']}:{s['port']} 비밀번호"
        d = PasswordDialog(self.root, prompt, s.get("remember_password", False))
        if d.result is None:
            return None
        pw, remember = d.result
        s["remember_password"] = remember
        if remember and pw:
            try:
                s["password_dpapi"] = protect_password(pw)
            except OSError as e:
                self.log("ERROR", f"비밀번호를 저장하지 못하였습니다: {e}")
        else:
            s["password_dpapi"] = ""
        self._save()
        return pw

    def _ensure_engine(self):
        """엔진이 돌고 있으면 True. 아니면 비밀번호를 구해 시작. 취소하면 False."""
        if self.engine.want:
            return True
        try:
            s = self._server_from_fields()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return False
        pw = self.engine.password
        if pw is None and s.get("password_dpapi"):
            try:
                pw = unprotect_password(s["password_dpapi"])
            except Exception:
                self.log("WARN", "저장된 비밀번호를 복호화하지 못하였습니다(다른 PC 또는 사용자에서 복사한 설정).")
                s["password_dpapi"] = ""
        if pw is None and s.get("auth") == "key" and not s.get("key_file"):
            messagebox.showerror(APP_NAME, "키 파일을 지정하십시오.", parent=self.root)
            return False
        if pw is None:
            pw = self._ask_password()
            if pw is None:
                return False
        self.engine.start(s, pw)
        self._show_state(self.engine.state)
        return True

    def _svc_guard(self):
        """서비스가 터널을 유지 중이면 안내하고 True."""
        if self.svc_mode:
            messagebox.showinfo(APP_NAME, "Windows 서비스가 터널을 유지하고 있습니다.\n\n"
                                          "• 터널 추가 · 편집 · '자동(✓)' 변경은 서비스가 몇 초 안에 자동 반영합니다.\n"
                                          "• 화면에서 직접 열려면 먼저 서비스를 중지하십시오.", parent=self.root)
            return True
        return False

    def toggle_connect(self):
        if not self.engine.want and self._svc_guard():
            return
        if self.engine.want:
            if self.fw and not messagebox.askyesno(APP_NAME, f"열린 터널 {len(self.fw)}건도 함께 닫힙니다. 계속하시겠습니까?",
                                                   parent=self.root):
                return
            self.stop_all()
        else:
            self._ensure_engine()

    def reconnect(self):
        if self.engine.want:
            self.log("INFO", "재연결을 요청하였습니다.")
            self.engine.reconnect_now()

    def forget_password(self):
        s = self.cfg["server"]
        s["password_dpapi"] = ""
        s["remember_password"] = False
        self.engine.password = None if not self.engine.want else self.engine.password
        self._save()
        self.log("INFO", "저장된 비밀번호를 삭제하였습니다.")

    def reset_hostkey(self):
        s = self.cfg["server"]
        names = {s["host"], f"[{s['host']}]:{s['port']}"}
        try:
            hk = paramiko.HostKeys(KNOWN_HOSTS) if os.path.exists(KNOWN_HOSTS) else paramiko.HostKeys()
        except Exception:
            hk = paramiko.HostKeys()
        n = 0
        for name in names:
            if name in hk:
                del hk[name]
                n += 1
        hk.save(KNOWN_HOSTS)
        self.log("INFO", f"호스트 키를 초기화하였습니다: {', '.join(sorted(names))} ({n}건 삭제). 다음 접속 시 지문을 다시 확인합니다.")
        sysk = os.path.expanduser("~/.ssh/known_hosts")
        if os.path.exists(sysk):
            self.log("INFO", f"※ {sysk} 에도 같은 서버가 있으면 ssh-keygen -R \"[{s['host']}]:{s['port']}\" 로 삭제하십시오.")

    # ---------------- 터널 제어
    def _sel(self):
        return sorted(int(i) for i in self.tree.selection())

    def start_tunnels(self, idxs):
        idxs = [i for i in idxs if i not in self.fw]
        if not idxs or self._svc_guard():
            return
        opened = []
        for i in idxs:
            t = self.cfg["tunnels"][i]
            f = Forwarder(self.engine, t)
            try:
                f.start()
            except PortInUse:
                owner = port_owner(t["local_port"])
                self.port_err[i] = f"포트 사용중 {owner}".strip()
                self.log("ERROR", f"[{t['name']}] 내 PC 포트 {t['local_port']}을(를) 다른 프로그램이 사용 중입니다 "
                                  f"{owner}. MobaXterm 터널 · Docker 등 해당 프로그램을 먼저 종료하십시오.")
                continue
            self.port_err.pop(i, None)
            self.fw[i] = f
            opened.append(i)
            self.log("OK", f"[{t['name']}] {t.get('bind', '127.0.0.1')}:{t['local_port']} → "
                           f"{t['remote_host']}:{t['remote_port']} 터널을 열었습니다.")
        if opened and not self._ensure_engine():
            for i in opened:
                self.fw.pop(i).stop()
            self.log("INFO", "인증이 취소되어 터널을 닫았습니다.")
        self._refresh_tree()

    def stop_tunnels(self, idxs):
        for i in idxs:
            f = self.fw.pop(i, None)
            if f:
                f.stop()
                self.log("INFO", f"[{f.tun['name']}] 터널을 중지하였습니다.")
            self.port_err.pop(i, None)
        if not self.fw and self.engine.want:
            self.engine.stop()
            self.log("INFO", "열린 터널이 없어 SSH 연결을 종료하였습니다.")
            self._show_state("끊김")
        self._refresh_tree()

    def start_selected(self):
        sel = self._sel()
        if not sel:
            messagebox.showinfo(APP_NAME, "터널을 선택하십시오.", parent=self.root)
            return
        self.start_tunnels(sel)

    def stop_selected(self):
        self.stop_tunnels(self._sel())

    def start_all(self):
        self.start_tunnels(list(range(len(self.cfg["tunnels"]))))

    def stop_all(self):
        self.stop_tunnels(list(self.fw.keys()))
        if self.engine.want:
            self.engine.stop()
            self._show_state("끊김")

    def _on_double(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        i = int(row)
        self.stop_tunnels([i]) if i in self.fw else self.start_tunnels([i])

    def _on_right(self, e):
        row = self.tree.identify_row(e.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.ctx.tk_popup(e.x_root, e.y_root)

    def copy_addr(self):
        sel = self._sel()
        if sel:
            t = self.cfg["tunnels"][sel[0]]
            self.root.clipboard_clear()
            self.root.clipboard_append(f"127.0.0.1:{t['local_port']}")
            self.log("INFO", f"클립보드에 복사하였습니다: 127.0.0.1:{t['local_port']}")

    def copy_ssh_cmd(self):
        s = self.cfg["server"]
        sel = self._sel() or list(range(len(self.cfg["tunnels"])))
        fwd = " ".join(f"-L {self.cfg['tunnels'][i]['local_port']}:{self.cfg['tunnels'][i]['remote_host']}:"
                       f"{self.cfg['tunnels'][i]['remote_port']}" for i in sel)
        cmd = (f"ssh -N -p {s['port']} -o ServerAliveInterval={s.get('keepalive_interval', 30)} "
               f"-o ServerAliveCountMax={s.get('keepalive_count', 3)} -o ExitOnForwardFailure=yes {fwd} "
               f"{s['user']}@{s['host']}")
        self.root.clipboard_clear()
        self.root.clipboard_append(cmd)
        self.log("INFO", f"ssh 명령을 클립보드에 복사하였습니다: {cmd}")

    # ---------------- 편집
    def _editable(self, i):
        if i in self.fw:
            messagebox.showinfo(APP_NAME, "열려 있는 터널은 먼저 중지하십시오.", parent=self.root)
            return False
        return True

    def _dup_port(self, port, skip=None):
        return any(t["local_port"] == port for j, t in enumerate(self.cfg["tunnels"]) if j != skip) \
            or any(t["local_port"] == port for t in self.cfg["hidden_tunnels"])

    def _hides(self, t):
        """숨긴 망(config.ini show_* = false)의 터널을 저장하려 하면 숨김 목록으로 보내고 알린다."""
        net = tunnel_net(t)
        if self.cfg["show"][net]:
            return False
        self.cfg["hidden_tunnels"].append(t)
        messagebox.showinfo(APP_NAME, f"'{t['name']}'은(는) {NET_LABEL[net]} 터널이므로 저장 후 목록에서 숨겨집니다.\n"
                                      f"표시하려면 {INI_PATH} 의 show_{net} 값을 true 로 변경한 후 다시 실행하십시오.", parent=self.root)
        return True

    def add_tunnel(self):
        d = TunnelDialog(self.root, "터널 추가")
        if d.result:
            if self._dup_port(d.result["local_port"]):
                messagebox.showerror(APP_NAME, f"내 PC 포트 {d.result['local_port']}은(는) 이미 다른 터널에서 사용 중입니다.",
                                     parent=self.root)
                return
            if self._hides(d.result):
                self._save()
                self._svc_changed()
                return
            self.cfg["tunnels"].append(d.result)
            self._unsorted()
            self._save()
            self._svc_changed()
            self._refresh_tree(full=True)

    def edit_tunnel(self):
        sel = self._sel()
        if not sel or not self._editable(sel[0]):
            return
        i = sel[0]
        d = TunnelDialog(self.root, "터널 편집", self.cfg["tunnels"][i])
        if d.result:
            if self._dup_port(d.result["local_port"], skip=i):
                messagebox.showerror(APP_NAME, f"내 PC 포트 {d.result['local_port']}은(는) 이미 다른 터널에서 사용 중입니다.",
                                     parent=self.root)
                return
            if self._hides(d.result):
                self.port_err.pop(i, None)
                self._reindex(lambda lst: [t for j, t in enumerate(lst) if j != i])
                return
            if d.result["name"] != self.cfg["tunnels"][i]["name"]:
                self._unsorted()
            self.cfg["tunnels"][i] = d.result
            self.port_err.pop(i, None)
            self._save()
            self._svc_changed()
            self._refresh_tree(full=True)

    def del_tunnel(self):
        sel = self._sel()
        if not sel or any(not self._editable(i) for i in sel):
            return
        names = ", ".join(self.cfg["tunnels"][i]["name"] for i in sel)
        if not messagebox.askyesno(APP_NAME, f"다음 터널을 삭제하시겠습니까?\n{names}", parent=self.root):
            return
        self._reindex(lambda lst: [t for j, t in enumerate(lst) if j not in sel])

    def move(self, d):
        sel = self._sel()
        if len(sel) != 1:
            return
        i, j = sel[0], sel[0] + d
        if not (0 <= j < len(self.cfg["tunnels"])):
            return

        def swap(lst):
            lst = list(lst)
            lst[i], lst[j] = lst[j], lst[i]
            return lst
        self._reindex(swap)
        self.tree.selection_set(str(j))
        self._unsorted()

    def _reindex(self, fn):
        """터널 목록 순서가 바뀔 때 열린 Forwarder 의 인덱스를 따라 옮긴다."""
        ids = [id(t) for t in self.cfg["tunnels"]]
        fw_by = {ids[i]: f for i, f in self.fw.items()}
        err_by = {ids[i]: m for i, m in self.port_err.items()}
        self.cfg["tunnels"] = fn(self.cfg["tunnels"])
        self.fw = {i: fw_by[id(t)] for i, t in enumerate(self.cfg["tunnels"]) if id(t) in fw_by}
        self.port_err = {i: err_by[id(t)] for i, t in enumerate(self.cfg["tunnels"]) if id(t) in err_by}
        self._save()
        self._svc_changed()
        self._refresh_tree(full=True)

    def _sort_mark(self):
        self.tree.heading("name", text="이름  " + {None: "⇅", False: "▲", True: "▼"}[self.sort_desc])

    def sort_by_name(self):
        """이름 머리글 클릭 — 오름 · 내림 번갈아. 정렬한 순서가 그대로 저장된다(▲▼ 로 다시 조정 가능)."""
        self.sort_desc = self.sort_desc is False
        sel = {id(self.cfg["tunnels"][i]) for i in self._sel()}
        self._reindex(lambda lst: sorted(lst, key=lambda t: name_sort_key(t["name"]), reverse=self.sort_desc))
        self.tree.selection_set([str(i) for i, t in enumerate(self.cfg["tunnels"]) if id(t) in sel])
        self._sort_mark()

    def _unsorted(self):
        if self.sort_desc is not None:
            self.sort_desc = None
            self._sort_mark()

    def apply_std_tunnels(self):
        # 숨긴 터널까지 합쳐서 비교해야 숨긴 표준 터널을 다시 추가하지 않는다
        preview = json.loads(json.dumps(self.cfg["tunnels"] + self.cfg["hidden_tunnels"]))
        renamed, added, skipped = merge_std_tunnels(preview)
        hid = {t["name"]: tunnel_net(t) for t in preview if not self.cfg["show"][tunnel_net(t)]}
        if not (renamed or added or skipped):
            messagebox.showinfo(APP_NAME, "이미 표준 터널 목록과 동일합니다.", parent=self.root)
            return
        lines = ([f"이름 변경 {len(renamed)}건"] + [f"  {a}  →  {b}" for a, b in renamed]
                 + [f"추가 {len(added)}건 (자동 ✓ 미설정 — 필요 시 편집에서 설정)"]
                 + [f"  {n}" + (f"  ({NET_LABEL[hid[n]]} — config.ini 로 숨김)" if n in hid else "")
                    for n in added])
        if skipped:
            lines += [f"건너뜀 {len(skipped)}건"] + [f"  {n} — {why}" for n, why in skipped]
        if not (renamed or added):
            messagebox.showinfo(APP_NAME, "\n".join(lines), parent=self.root)
            return
        if not messagebox.askyesno(APP_NAME, "표준 터널 목록을 반영하시겠습니까?\n\n" + "\n".join(lines), parent=self.root):
            return
        both = self.cfg["tunnels"] + self.cfg["hidden_tunnels"]
        n0 = len(both)
        merge_std_tunnels(both)                     # 이름은 제자리 수정 — 열린 터널의 인덱스는 그대로
        for t in both[n0:]:                         # 새로 추가된 것만 보이는 / 숨긴 목록으로 나눈다
            (self.cfg["tunnels"] if self.cfg["show"][tunnel_net(t)] else self.cfg["hidden_tunnels"]).append(t)
        for a, b in renamed:
            self.log("INFO", f"이름을 변경하였습니다: {a} → {b}")
        for n in added:
            self.log("INFO", f"터널을 추가하였습니다: {n}")
        for n, why in skipped:
            self.log("WARN", f"추가하지 않았습니다: {n} — {why}")
        self._save()
        self._svc_changed()
        self._unsorted()
        self._refresh_tree(full=True)

    # ---------------- 점검
    def check_ports(self):
        self.log("INFO", "포트 점검을 시작합니다.")
        for i, t in enumerate(self.cfg["tunnels"]):
            p = t["local_port"]
            if i in self.fw:
                self.log("OK", f"  {p:>5}  본 프로그램에서 사용 중 — {t['name']}")
            elif port_free(t.get("bind", "127.0.0.1"), p):
                self.log("OK", f"  {p:>5}  비어 있음 — {t['name']}")
                self.port_err.pop(i, None)
            else:
                owner = port_owner(p)
                self.port_err[i] = f"포트 사용중 {owner}".strip()
                self.log("ERROR", f"  {p:>5}  사용 중 {owner or '(프로세스 확인 불가)'} — {t['name']}")
        self._refresh_tree()

    def test_targets(self):
        sel = self._sel() or list(range(len(self.cfg["tunnels"])))
        if not self.engine.connected.is_set():
            if self.svc_mode:
                messagebox.showinfo(APP_NAME, "서비스 실행 중에는 표의 상태 · 최근 메시지와 서비스 [로그]로 확인하십시오.\n"
                                              "대상 실패는 '최근 메시지' 에 빨간 행으로 표시됩니다.", parent=self.root)
                return
            messagebox.showinfo(APP_NAME, "SSH 서버에 연결된 상태에서만 시험할 수 있습니다.\n터널을 하나 이상 시작하거나 [연결]을 누르십시오.",
                                parent=self.root)
            return
        tuns = [dict(self.cfg["tunnels"][i]) for i in sel]
        self.log("INFO", f"대상 연결 시험을 시작합니다: {len(tuns)}건 (SSH 서버 → 원격 대상)")

        def work():
            for t in tuns:
                t0 = time.time()
                ch, err = self.engine.open_channel(t["remote_host"], t["remote_port"], ("127.0.0.1", 0), wait=5)
                if ch:
                    ch.close()
                    self.engine.log("OK", f"  성공 {t['remote_host']}:{t['remote_port']} "
                                          f"({(time.time() - t0) * 1000:.0f}ms) — {t['name']}")
                else:
                    self.engine.log("ERROR", f"  실패 {t['remote_host']}:{t['remote_port']} — {t['name']}: {err}")
        threading.Thread(target=work, daemon=True).start()

    # ---------------- 표 갱신
    def _svc_rows(self):
        """서비스가 알려 준 터널 상태 {local_port: row} — 10초 넘게 갱신이 없으면 None."""
        st = self.svc_st
        if not st or st.get("stopped") or time.time() - st.get("updated", 0) > 10:
            return None
        return {r["local_port"]: r for r in st.get("tunnels", [])}

    def _row(self, i, t):
        if self.svc_mode:
            rows = self._svc_rows()
            base = (("✓" if t.get("auto") else ""), t["name"], f"{t.get('bind', '127.0.0.1')}:{t['local_port']}",
                    f"{t['remote_host']}:{t['remote_port']}")
            if rows is None:
                return base + ("서비스 응답 대기", "", "", ""), "wait"
            r = rows.get(int(t["local_port"]))
            if not t.get("auto"):
                return base + ("서비스 대상 아님", "", "", "자동(✓)이 아니므로 서비스가 열지 않습니다"), "off"
            if r is None:
                return base + ("반영 대기", "", "", "서비스가 설정을 읽고 있습니다"), "wait"
            tag = "err" if r.get("err") else ("open" if r["status"] == "열림" else "wait")
            return base + ("서비스 · " + r["status"], f"{r['active']} / {r['total']}",
                           f"{fmt_bytes(r['up'])} / {fmt_bytes(r['down'])}", r.get("msg", "")), tag
        f = self.fw.get(i)
        if f:
            if not self.engine.connected.is_set():
                state, tag = "열림(서버 대기)", "wait"
            else:
                state, tag = "열림", "err" if f.last_err else "open"
            conns = f"{f.active} / {f.total}"
            traffic = f"{fmt_bytes(f.up)} / {fmt_bytes(f.down)}"
            msg = f.last_msg
        else:
            err = self.port_err.get(i)
            state, tag = ("포트 사용중", "err") if err else ("중지", "off")
            conns, traffic, msg = "", "", err or ""
        return (("✓" if t.get("auto") else ""), t["name"], f"{t.get('bind', '127.0.0.1')}:{t['local_port']}",
                f"{t['remote_host']}:{t['remote_port']}", state, conns, traffic, msg), tag

    def _refresh_tree(self, full=False):
        if full:
            sel = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            for i, t in enumerate(self.cfg["tunnels"]):
                vals, tag = self._row(i, t)
                self.tree.insert("", "end", iid=str(i), values=vals, tags=(tag,), image=self.ic.get("dot_" + tag))
            keep = [s for s in sel if self.tree.exists(s)]
            if keep:
                self.tree.selection_set(keep)
        else:
            for i, t in enumerate(self.cfg["tunnels"]):
                vals, tag = self._row(i, t)
                self.tree.item(str(i), values=vals, tags=(tag,), image=self.ic.get("dot_" + tag))
        if self.svc_mode:
            rows = self._svc_rows() or {}
            n_open = sum(1 for r in rows.values() if r["status"].startswith("열림"))
            n_conn = sum(r["active"] for r in rows.values())
            self.status.configure(text=f"서비스 모드 · 열린 터널 {n_open} / {len(self.cfg['tunnels'])} · 활성 연결 {n_conn}"
                                       f"{self._hidden_note()} · 설정 {CONF_PATH}")
            return
        n_open = len(self.fw)
        n_conn = sum(f.active for f in self.fw.values())
        up = ""
        if self.engine.connected.is_set() and self.engine.connected_at:
            sec = int(time.time() - self.engine.connected_at)
            up = f" · 연결 유지 {sec // 3600}시간 {sec % 3600 // 60}분"
        self.status.configure(text=f"열린 터널 {n_open} / {len(self.cfg['tunnels'])} · 활성 연결 {n_conn}{up}"
                                   f"{self._hidden_note()} · 설정 {CONF_PATH}")

    def _hidden_note(self):
        n = len(self.cfg["hidden_tunnels"])
        return f" · 숨김 {self._hidden_summary()}(config.ini)" if n else ""

    def _hidden_summary(self):
        cnt = {}
        for t in self.cfg["hidden_tunnels"]:
            k = tunnel_net(t)
            cnt[k] = cnt.get(k, 0) + 1
        return " · ".join(f"{NET_LABEL[k]} {cnt[k]}건" for k in NETS if k in cnt)

    def open_ini(self):
        ensure_ini()
        try:
            subprocess.Popen(["notepad.exe", INI_PATH], creationflags=NO_WINDOW)
        except OSError:
            os.startfile(INI_PATH)
        self.log("INFO", f"config.ini 를 수정한 후 화면을 다시 실행하면 반영됩니다(서비스는 2초 이내 반영): {INI_PATH}")

    def _tick(self):
        self._tick_n = getattr(self, "_tick_n", 0) + 1
        if self._tick_n % 2 == 0:
            self._svc_poll()
        self._refresh_tree()
        self.root.after(1000, self._tick)

    # ---------------- 윈도우 서비스
    def _svc_poll(self):
        prev = self.svc_state
        try:
            self.svc_state, self.svc_info = svc_query()
        except Exception:
            self.svc_state, self.svc_info = -1, {}
        try:
            with open(STATUS_PATH, encoding="utf-8") as f:
                self.svc_st = json.load(f)
        except (OSError, ValueError):
            self.svc_st = None
        if prev != self.svc_state and prev != -1:
            self.log("INFO", "서비스 상태: " + ("설치 안 됨" if self.svc_state is None else SVC_STATES.get(self.svc_state, "확인 불가")))
        self._svc_show()

    def _svc_show(self):
        st, info = self.svc_state, self.svc_info
        note = ""
        if st is None:
            color, text = "#666666", "설치되지 않음 — [설치] 시 PC 를 켤 때마다 자동(✓) 터널을 열고 유지합니다(관리자 승인 1회)."
        elif st == -1:
            color, text = "#666666", "서비스 상태를 확인할 수 없습니다"
        else:
            color = {4: "#1a7f37", 1: "#666666"}.get(st, "#9a6700")
            text = SVC_STATES.get(st, str(st))
            if st == 4:
                text += f" (PID {info.get('pid')})"
                sst = self.svc_st if (self.svc_st and time.time() - self.svc_st.get("updated", 0) < 10) else None
                if sst:
                    rows = sst.get("tunnels", [])
                    n_open = sum(1 for r in rows if r["status"].startswith("열림"))
                    text += f" · SSH {sst.get('ssh_state')} · 터널 {n_open}/{len(rows)} 열림"
                    if sst.get("connected_since"):
                        sec = int(time.time() - sst["connected_since"])
                        text += f" · 연결 유지 {sec // 3600}시간 {sec % 3600 // 60}분"
                    if sst.get("ssh_state") in ("인증 실패", "비밀번호 없음", "호스트 키 불일치") or "호스트 키" in str(sst.get("ssh_state")):
                        color = "#cf222e"
                else:
                    text += " · 서비스 응답 대기"
            elif st == 1:
                text += " · PC 시작 시 자동 실행" if info.get("start") == 2 else ""
            exe_cmd = f'"{sys.executable}" --service-run' if FROZEN else None
            if exe_cmd and info.get("binpath") and info["binpath"].lower() != exe_cmd.lower():
                note = f"※ 서비스가 다른 위치의 exe 를 실행하고 있습니다: {info['binpath']}. 이 exe 로 변경하려면 [갱신]을 누르십시오."
            elif not self.cfg["server"].get("password_svc") and self.cfg["server"].get("auth") != "key":
                note = "※ 서비스용 비밀번호가 저장되어 있지 않습니다. [비밀번호]로 저장하십시오."
            elif st == 4 and self.svc_st and "호스트 키" in str(self.svc_st.get("ssh_state")):
                note = "※ 서비스용 호스트 키가 없거나 일치하지 않습니다. [비밀번호]를 다시 저장하면 확인된 키가 서비스에 등록됩니다."
        self.svc_dot.configure(fg=color)
        self.svc_lbl.configure(text=text)
        self.svc_note.configure(text=note)
        busy = self.svc_busy
        en = {"install": True, "start": st == 1, "stop": st in (2, 4), "password": True,
              "log": True, "uninstall": st not in (None, -1)}
        for k, b in self.svc_btns.items():
            b.configure(state="normal" if en[k] and not busy else "disabled")
        self.svc_btns["install"].configure(text=" 설치" if st is None else " 갱신",
                                          image=self.ic.get("install" if st is None else "refresh"))

    def _bg(self, fn, done):
        """fn 을 작업 스레드에서 돌리고 done(결과, 예외) 를 화면 스레드에서 부른다."""
        def work():
            try:
                r, e = fn(), None
            except Exception as ex:
                r, e = None, ex
            self.events.put(("call", lambda: done(r, e)))
        threading.Thread(target=work, daemon=True).start()

    def _set_busy(self, busy):
        self.svc_busy = busy
        self._svc_show()

    def _ask_svc_password(self):
        s = self.cfg["server"]
        if s.get("auth") == "key":
            prompt = f"서비스용 키 파일 암호(passphrase)를 입력하십시오. 암호가 없으면 비워 두십시오.\n{s.get('key_file')}"
        else:
            prompt = (f"서비스용 비밀번호 — {s['user']}@{s['host']}:{s['port']}\n"
                      "서비스는 로그인 전에 실행되므로 이 PC 에 암호화하여 저장합니다.")
        d = PasswordDialog(self.root, prompt, False, show_remember=False, title="서비스 비밀번호")
        return None if d.result is None else d.result[0]

    def _sync_svc_hostkey(self):
        """서비스가 설치돼 있으면, 사용자 known_hosts 에서 이미 믿고 쓰던 서버 키를 서비스용 known_hosts 로 옮긴다."""
        if self.svc_state in (None, -1):
            return
        s = self.cfg["server"]
        try:
            r = sync_known_hosts(s["host"], s["port"])
        except Exception as e:
            self.log("WARN", f"서비스용 호스트 키를 확인하지 못하였습니다: {e}")
            return
        if r:
            self.log("OK", f"서비스용 호스트 키를 등록하였습니다: {host_key_name(s['host'], s['port'])} {r[0]} (출처 {r[1]}) "
                           "— 서비스가 2초 이내에 다시 접속합니다.")

    def _verify_login(self, server, pw):
        """접속 · 인증 · 호스트 키 등록 확인(처음이면 지문 확인 창). 성공 시 서버 버전 문자열."""
        eng = Engine(self.events, sync_known=True)     # 성공하면 확인된 서버 키를 서비스용 known_hosts 에 저장
        eng.server, eng.password = dict(server), pw
        eng._connect()
        ver = eng.transport.remote_version
        eng._drop()
        return ver

    def _save_svc_password(self, then=None):
        try:
            s = self._server_from_fields()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return
        if s.get("auth") == "key" and not s.get("key_file"):
            messagebox.showerror(APP_NAME, "키 파일을 지정하십시오.", parent=self.root)
            return
        pw = self._ask_svc_password()
        if pw is None:
            return
        self.log("INFO", f"로그인을 확인하고 있습니다: {s['user']}@{s['host']}:{s['port']}")
        self._set_busy(True)

        def done(ver, err):
            self._set_busy(False)
            if err:
                why = "비밀번호(또는 키 암호)가 일치하지 않습니다." if isinstance(err, paramiko.AuthenticationException) else \
                    f"{type(err).__name__}: {err}"
                self.log("ERROR", f"로그인 확인에 실패하였습니다: {why}")
                messagebox.showerror(APP_NAME, f"로그인 확인에 실패하여 비밀번호를 저장하지 않았습니다.\n\n{why}", parent=self.root)
                return
            s["password_svc"] = protect_password(pw, machine=True) if pw else ""
            self._save()
            self.log("OK", f"로그인을 확인하고({ver}) 서비스용 비밀번호를 저장하였습니다(PC 범위 암호화).")
            if then:
                then()
            elif self.svc_mode:
                self.log("INFO", "서비스가 수 초 이내에 새 비밀번호로 다시 연결합니다.")
            self._svc_show()
        self._bg(lambda: self._verify_login(s, pw), done)

    def _elevated(self, action, label, after=None):
        self.log("INFO", f"{label}: 관리자 승인 창(UAC)이 표시되면 [예]를 누르십시오.")
        self._set_busy(True)

        def done(r, err):
            self._set_busy(False)
            code, werr = r if r else (None, str(err))
            if code is None:
                if werr == 1223:
                    self.log("WARN", f"{label}: 관리자 승인이 취소되었습니다.")
                else:
                    self.log("ERROR", f"{label}: 관리자 권한으로 실행하지 못하였습니다({werr}).")
            elif code == 0:
                self.log("OK", f"{label}: 완료되었습니다.")
                if after:
                    after()
            else:
                self.log("ERROR", f"{label}: 실패하였습니다(코드 {code}). 관리 로그의 마지막 부분은 다음과 같습니다.")
                p = os.path.join(LOG_DIR, f"service_admin_{dt.date.today():%Y%m%d}.log")
                try:
                    with open(p, encoding="utf-8") as f:
                        for line in f.read().splitlines()[-6:]:
                            self.log("ERROR", "  " + line[20:])
                except OSError:
                    pass
            self._svc_poll()
            self._refresh_tree()
        self._bg(lambda: run_elevated(f"--service {action}"), done)

    def svc_install(self):
        if self.svc_busy:
            return
        if not FROZEN:
            messagebox.showinfo(APP_NAME, "서비스는 빌드된 CaptainTunnel.exe에서 설치하십시오.", parent=self.root)
            return
        if os.path.normcase(APP_DIR) != os.path.normcase(EXE_DIR):
            messagebox.showerror(APP_NAME, f"이 폴더에는 설정을 저장할 수 없어 서비스로 설치할 수 없습니다.\n{EXE_DIR}\n\n"
                                           "내 계정이 쓸 수 있는 폴더(예: D:\\tools\\CaptainTunnel)로 프로그램 폴더를 옮긴 후 다시 시도하십시오.",
                                 parent=self.root)
            return
        n = sum(1 for t in self.cfg["tunnels"] if t.get("auto"))
        if not n:
            messagebox.showerror(APP_NAME, "자동(✓) 터널이 없습니다. 터널 편집에서 '자동 시작 대상'을 선택하십시오.", parent=self.root)
            return
        exe = sys.executable if FROZEN else f"{sys.executable} {source_launcher()}"
        msg = (f"Windows 서비스 '{SVC_NAME}' 로 {'설치' if self.svc_state is None else '갱신'}합니다.\n\n"
               f"• 대상: 자동(✓) 터널 {n}건 — PC 를 켜면 로그인 전에 자동으로 열고, 끊기면 다시 연결합니다.\n"
               f"• 실행 파일: {exe}\n  이 폴더를 옮기거나 삭제하면 서비스가 동작하지 않습니다(이동한 경우 다시 설치하십시오).\n"
               "• 비밀번호는 이 PC 에 암호화하여 저장합니다(PC 범위 — 이 PC 의 프로그램은 복호화할 수 있습니다).\n"
               "• 설치 · 제거만 관리자 승인(UAC)이 필요하고, 시작 · 중지는 승인 없이 가능합니다.\n")
        if self.fw:
            msg += f"• 화면에서 연 터널 {len(self.fw)}건은 닫고 서비스로 이관합니다.\n"
        if not FROZEN:
            msg += "\n※ 개발 실행(python)입니다. 배포 exe 로 설치하는 것을 권장합니다.\n"
        if not messagebox.askyesno(APP_NAME + " — 서비스 설치", msg + "\n계속하시겠습니까?", parent=self.root):
            return

        def install():
            if self.fw or self.engine.want:
                self.stop_all()
            self._elevated("install", "서비스 설치")
        self._save_svc_password(then=install)

    def svc_password(self):
        if not self.svc_busy:
            self._save_svc_password()

    def svc_start_stop(self, action):
        if self.svc_busy:
            return
        label = "서비스 시작" if action == "start" else "서비스 중지"
        if action == "start" and (self.fw or self.engine.want):
            if not messagebox.askyesno(APP_NAME, f"화면에서 연 터널 {len(self.fw)}건을 닫고 서비스로 이관하시겠습니까?", parent=self.root):
                return
            self.stop_all()
        if action == "stop" and not messagebox.askyesno(
                APP_NAME, "서비스를 중지하면 서비스가 연 터널이 모두 닫힙니다.\n(PC 를 다시 켜면 서비스는 다시 자동 실행됩니다)\n\n중지하시겠습니까?",
                parent=self.root):
            return
        self._set_busy(True)

        def done(rc, err):
            self._set_busy(False)
            if rc == 5:
                self._elevated(action, label)      # 권한 없음 → 관리자 승인으로
                return
            if err or rc:
                self.log("ERROR", f"{label}: 실패하였습니다({err or f'Win32 오류 {rc}'}).")
            else:
                self.log("OK", f"{label}: 요청을 완료하였습니다.")
            self._svc_poll()
        self._bg(lambda: svc_control(action), done)

    def svc_uninstall(self):
        if self.svc_busy or self.svc_state is None:
            return
        if not messagebox.askyesno(APP_NAME, "서비스를 제거하시겠습니까?\n\n서비스가 연 터널이 닫히며, PC 를 켤 때 자동 실행되지 않습니다.\n"
                                             "저장된 서비스용 비밀번호도 삭제합니다.", parent=self.root):
            return

        def after():
            self.cfg["server"]["password_svc"] = ""
            self._save()
        self._elevated("uninstall", "서비스 제거", after)

    def svc_open_log(self):
        logs = sorted(n for n in os.listdir(LOG_DIR) if n.startswith("service_2")) if os.path.isdir(LOG_DIR) else []
        if logs:
            os.startfile(os.path.join(LOG_DIR, logs[-1]))
        else:
            messagebox.showinfo(APP_NAME, "서비스 로그가 아직 없습니다.", parent=self.root)

    def _svc_changed(self):
        if self.svc_mode:
            self.log("INFO", "설정을 저장하였습니다. 서비스가 2초 이내에 변경된 터널만 다시 엽니다.")

    def _login_changed(self):
        try:
            cmd = login_autostart(self.login_var.get())
            self.log("OK", f"로그인 시 자동 실행을 등록하였습니다: {cmd}" if cmd else "로그인 시 자동 실행을 해제하였습니다.")
            if cmd and not self.cfg.get("autostart_on_launch"):
                self.log("INFO", "로그인 자동 실행 시에는 자동(✓) 터널을 바로 엽니다(비밀번호 기억 필요).")
        except OSError as e:
            self.log("ERROR", f"로그인 자동 실행을 설정하지 못하였습니다: {e}")

    # ---------------- 기타
    def show_help(self):
        messagebox.showinfo(APP_NAME + " — 사용 방법", (
            "1) 경유 SSH 서버(주소 · 포트 · 계정)를 확인합니다.\n"
            "2) 터널을 선택해 ▶ 시작(또는 더블클릭) · 전체 시작을 누릅니다.\n"
            "3) 처음 접속이면 호스트 키 지문을 확인하고, 비밀번호를 입력합니다.\n"
            "4) 상태가 '열림'(초록)이 되면 DB 도구 · 브라우저 · ssh 에서\n"
            "   127.0.0.1:<내 PC 포트> 로 접속합니다.\n\n"
            "• SSH 접속 1회로 모든 터널이 함께 열립니다(MobaXterm 은 터널마다 접속).\n"
            "• 끊기면 자동 재연결합니다. 재연결 동안 들어온 접속은 최대 20초 기다립니다.\n"
            "• '포트 사용중' 이면 MobaXterm 터널 · 도커 등 해당 포트를 사용하는 프로그램을 종료하십시오.\n"
            "• 대상 연결 실패는 SSH 서버에서 원격 대상까지 연결되지 않는 경우입니다(대상 서비스 중지 등).\n"
            "• 원격 대상 127.0.0.1 은 SSH 서버 자신입니다.\n"
            "• 창을 닫으면 화면에서 연 터널이 닫힙니다.\n\n"
            "[PC 를 켤 때 자동으로 유지 — 윈도우 서비스]\n"
            "• 서비스 [설치] (관리자 승인 1회) → 로그인 전부터 '자동(✓)' 터널을 열고 유지합니다.\n"
            "• 서비스 실행 중에는 이 화면을 닫아도 터널이 유지되고, 터널 편집은 서비스가 자동 반영합니다.\n"
            "• 처음 한 번은 화면에서 연결해 호스트 키를 등록해 두어야 합니다(설치 때 자동 확인).\n\n"
            f"설정: {CONF_PATH}\n로그: {LOG_DIR}"), parent=self.root)

    def on_close(self):
        if self.fw and not messagebox.askyesno(APP_NAME, f"열린 터널 {len(self.fw)}건을 모두 닫고 종료하시겠습니까?",
                                               parent=self.root):
            return
        try:
            self._server_from_fields()
        except ValueError:
            pass
        for f in self.fw.values():
            f.stop()
        self.engine.stop()
        self.log("INFO", "프로그램을 종료합니다.")
        self.root.destroy()


def single_instance():
    if not IS_WIN:
        return True
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    single_instance.h = k.CreateMutexW(None, False, "Local\\CaptainTunnel_SingleInstance")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def main():
    argv = sys.argv[1:]
    if "--service-run" in argv:
        sys.exit(service_dispatch())
    if argv[:1] == ["--service"]:
        sys.exit(service_admin(argv[1] if len(argv) > 1 else ""))
    if "--headless" in argv:
        sys.exit(headless_main())
    if IS_WIN:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    if not single_instance():
        root.withdraw()
        messagebox.showwarning(APP_NAME, "캡틴 터널링이 이미 실행 중입니다.\n작업 표시줄의 기존 창을 사용하십시오.")
        return
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family="Malgun Gothic", size=10)
        except tk.TclError:
            pass
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    lh = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    style.configure("Treeview", rowheight=int(lh * 1.7))
    style.configure("Treeview.Heading", font=("Malgun Gothic", 10, "bold"))
    icon = icon_path()
    if os.path.exists(icon):
        try:
            root.iconbitmap(icon)
        except tk.TclError:
            pass
    global SCALE
    SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    App(root, autostart="--autostart" in argv)
    root.geometry(f"{int(1200 * SCALE)}x{int(760 * SCALE)}")
    if "--minimized" in argv:
        root.iconify()
    root.mainloop()


if __name__ == "__main__":
    main()
