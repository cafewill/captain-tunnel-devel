# -*- coding: utf-8 -*-
"""엔진 · 중계 자체 시험 (GUI 없이).

paramiko 로 띄운 시험용 SSH 서버(비밀번호 cloud/test, direct-tcpip 허용)와 에코 서버를 쓴다.
시험: 연결·중계 / 대용량 / 동시 접속 / 대상 연결 실패 / 포트 점유 / 인증 실패 / 서버 재시작 후 자동 재연결
실행: python tests\\integration_engine.py
"""
import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import paramiko  # noqa: E402

from captain_tunnel import application as it  # noqa: E402

it.KNOWN_HOSTS = os.path.join(tempfile.mkdtemp(), "known_hosts")
it.USER_KNOWN_HOSTS = os.path.join(tempfile.mkdtemp(), "user_known_hosts")   # 실제 사용자 파일과 분리
it.INI_PATH = os.path.join(tempfile.mkdtemp(), "config.ini")                 # src 폴더에 config.ini 를 만들지 않게
HOSTKEY = paramiko.RSAKey.generate(2048)


# ---------------------------------------------------------- 시험용 서버
def echo_server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(50)

    def h(c):
        with c:
            while True:
                d = c.recv(65536)
                if not d:
                    break
                c.sendall(d)

    def loop():
        while True:
            c, _ = s.accept()
            threading.Thread(target=h, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return s.getsockname()[1]


class SrvIface(paramiko.ServerInterface):
    def check_auth_password(self, u, p):
        return paramiko.AUTH_SUCCESSFUL if (u, p) == ("cloud", "test") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, u):
        return "password"

    def check_channel_request(self, kind, cid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_direct_tcpip_request(self, cid, origin, dest):
        # sshd 처럼 대상에 먼저 붙어 보고 실패면 open failure 로 응답
        try:
            self.sock = socket.create_connection(dest, timeout=3)
        except OSError:
            return paramiko.OPEN_FAILED_CONNECT_FAILED
        return paramiko.OPEN_SUCCEEDED

    def check_global_request(self, kind, msg):
        return False


class SshServer:
    def __init__(self, port=0, no_global_reply=False):
        self.no_global_reply = no_global_reply     # keepalive(global request)에 응답하지 않는 게이트웨이 흉내
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(20)
        self.port = self.sock.getsockname()[1]
        self.transports = []
        self.alive = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.alive:
            try:
                c, _ = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        t = paramiko.Transport(c)
        t.add_server_key(HOSTKEY)
        if self.no_global_reply:
            from paramiko.common import MSG_GLOBAL_REQUEST
            t._handler_table[MSG_GLOBAL_REQUEST] = lambda m: None
        iface = SrvIface()
        try:
            t.start_server(server=iface)
        except Exception:
            return
        self.transports.append(t)
        while t.is_active():
            ch = t.accept(1)
            if ch is None:
                continue
            threading.Thread(target=self._fwd, args=(ch, iface.sock), daemon=True).start()

    def _fwd(self, ch, s):
        import select
        s.settimeout(None)
        watch = [s, ch]
        try:
            while watch:
                r, _, _ = select.select(watch, [], [])
                if s in r:
                    d = s.recv(65536)
                    if not d:
                        break
                    ch.sendall(d)
                if ch in r:
                    d = ch.recv(65536)
                    if d:
                        s.sendall(d)
                    else:  # half-close 전달
                        s.shutdown(socket.SHUT_WR)
                        watch.remove(ch)
        except OSError:
            pass
        ch.close()
        s.close()

    def kill(self):
        self.alive = False
        self.sock.close()
        for t in self.transports:
            t.close()


# ---------------------------------------------------------- 도우미
def drain(q, out):
    while True:
        try:
            ev = q.get(timeout=0.1)
        except queue.Empty:
            continue
        if ev[0] == "hostkey":
            ev[5]["ok"] = True
            ev[4].set()
        out.append(ev)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def roundtrip(port, payload):
    with socket.create_connection(("127.0.0.1", port), timeout=30) as c:
        c.sendall(payload)
        c.shutdown(socket.SHUT_WR)
        buf = b""
        while True:
            d = c.recv(65536)
            if not d:
                break
            buf += d
    return buf


def wait_state(eng, want, sec=15):
    end = time.time() + sec
    while time.time() < end:
        if eng.state == want:
            return True
        time.sleep(0.1)
    return False


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def main():
    it.Engine.BACKOFF = [1, 1, 2]
    echo = echo_server()
    srv = SshServer()
    q, evs = queue.Queue(), []
    threading.Thread(target=drain, args=(q, evs), daemon=True).start()
    eng = it.Engine(q)
    server = {"host": "127.0.0.1", "port": srv.port, "user": "cloud", "auth": "password",
              "keepalive_interval": 5, "keepalive_count": 2}

    lp = free_port()
    fw = it.Forwarder(eng, {"name": "echo", "local_port": lp, "remote_host": "127.0.0.1", "remote_port": echo})
    fw.start()
    eng.start(server, "test")
    check("연결됨", wait_state(eng, "연결됨"), eng.state)
    check("호스트 키 등록", any(e[0] == "hostkey" for e in evs) and os.path.getsize(it.KNOWN_HOSTS) > 0)

    check("왕복 소량", roundtrip(lp, b"hello") == b"hello")
    big = os.urandom(8 * 1024 * 1024)
    t0 = time.time()
    check("왕복 8MB", roundtrip(lp, big) == big, f"{time.time() - t0:.1f}s")

    res = []
    ths = [threading.Thread(target=lambda i=i: res.append(roundtrip(lp, bytes([i]) * 100000) == bytes([i]) * 100000))
           for i in range(20)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    check("동시 20건", len(res) == 20 and all(res))
    check("통계 집계", fw.total >= 22 and fw.up > 8_000_000, f"total={fw.total} up={fw.up}")

    # 대상 연결 실패
    lp2 = free_port()
    fw2 = it.Forwarder(eng, {"name": "dead", "local_port": lp2, "remote_host": "127.0.0.1", "remote_port": free_port()})
    fw2.start()
    try:
        r = roundtrip(lp2, b"x")
    except OSError:
        r = b""
    time.sleep(0.5)
    check("대상 연결 실패 표시", r == b"" and fw2.last_err and "대상 연결 실패" in fw2.last_msg, fw2.last_msg)
    fw2.stop()

    # 포트 점유
    fw3 = it.Forwarder(eng, {"name": "dup", "local_port": lp, "remote_host": "127.0.0.1", "remote_port": echo})
    try:
        fw3.start()
        dup = False
    except it.PortInUse:
        dup = True
    check("포트 점유 감지", dup)
    check("점유 프로세스 조회", "python" in it.port_owner(lp).lower(), it.port_owner(lp))

    # keepalive 응답 유지
    time.sleep(12)
    check("keepalive 유지(12초)", eng.state == "연결됨" and eng.last_ka_ok > time.time() - 8, eng.state)

    # 서버 재시작 → 자동 재연결
    port = srv.port
    srv.kill()
    check("끊김 감지", wait_state(eng, "재연결 대기", 10) or eng.state in ("재연결 중", "재연결 대기"), eng.state)
    srv = SshServer(port)
    check("자동 재연결", wait_state(eng, "연결됨", 15), eng.state)
    check("재연결 후 왕복", roundtrip(lp, b"again") == b"again")

    # 재연결 중 들어온 접속 대기
    srv.kill()
    wait_state(eng, "재연결 대기", 10)
    out = []
    th = threading.Thread(target=lambda: out.append(roundtrip(lp, b"queued")))
    th.start()
    time.sleep(1)
    srv = SshServer(port)
    th.join(30)
    check("재연결 동안 접속 대기 후 성공", out == [b"queued"], str(out))

    eng.stop()
    fw.stop()
    check("중지 후 포트 해제", it.port_free("127.0.0.1", lp))

    # 인증 실패 → 재시도 안 함
    evs.clear()
    eng2 = it.Engine(q)
    eng2.start(server, "wrong")
    check("인증 실패 상태", wait_state(eng2, "인증 실패", 10), eng2.state)
    time.sleep(3)
    check("인증 실패 후 재시도 없음", sum(1 for e in evs if e[0] == "log" and "접속을 시도" in e[2]) == 1)
    check("authfail 이벤트", any(e[0] == "authfail" for e in evs))

    # DPAPI
    enc = it.protect_password("비밀!pw")
    check("DPAPI 왕복", it.unprotect_password(enc) == "비밀!pw" and "비밀" not in enc)

    enc = it.protect_password("svc-pw", machine=True)
    check("DPAPI(PC 범위) 왕복", it.unprotect_password(enc) == "svc-pw")

    srv.kill()
    test_hostkey_sync(echo)
    test_keepalive_no_reply()
    test_runner(echo)
    test_std_tunnels()
    test_net_ini()
    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} 통과" + (f" · 실패: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)


# ---------------------------------------------------------- 표준 터널 목록 · 이름 정렬 (2026-10-27)
def test_std_tunnels():
    std = it.DEFAULT_CONFIG["tunnels"]
    check("표준: 이름 앞 구분자 [DB]·[SSH]·[WEB]", all(t["name"].split("]")[0] in ("[DB", "[SSH", "[WEB") for t in std))
    check("표준: 망 접두사 공사망·개발망·MIG", all(t["name"].split()[1] in ("공사망", "개발망", "MIG") for t in std))
    check("표준: 내 PC 포트 중복 없음", len({t["local_port"] for t in std}) == len(std))
    check("표준: 원격 대상 중복 없음", len({(t["remote_host"], t["remote_port"]) for t in std}) == len(std))
    check("표준: 172.17.155.x 는 공사망", all(("공사망" in t["name"]) == t["remote_host"].startswith("172.17.155.")
                                         for t in std if t["remote_host"] != "127.0.0.1"))

    names = ["[WEB] 공사망 매퍼 운영", "[DB] 공사망 b", "[ssh] x", "[DB] 개발망 a", "[DB] 개발망 port 38650", "[DB] 개발망 port 8090"]
    asc = sorted(names, key=it.name_sort_key)
    check("정렬: 구분자 → 망 순 · 대소문자 무시 · 숫자 크기순",
          asc == ["[DB] 개발망 a", "[DB] 개발망 port 8090", "[DB] 개발망 port 38650", "[DB] 공사망 b", "[ssh] x",
                  "[WEB] 공사망 매퍼 운영"], str(asc))
    check("정렬: 내림은 오름의 역순", sorted(names, key=it.name_sort_key, reverse=True) == asc[::-1])

    # 2026-10-22 판 기본값(옛 이름) + 현장에서 손으로 넣은 8090 + 사용자 고유 터널
    old = [{"name": "공사망 자료적재", "local_port": 8222, "remote_host": "172.17.155.113", "remote_port": 5222, "auto": True},
           {"name": "운영망 매퍼배포", "local_port": 9222, "remote_host": "172.17.155.132", "remote_port": 5222, "auto": True},
           {"name": "운영망 항행정보 (NST FMSOWN)", "local_port": 38651, "remote_host": "172.17.155.115", "remote_port": 38650, "auto": True},
           {"name": "MIG 매퍼 및 에어플로", "local_port": 8080, "remote_host": "127.0.0.1", "remote_port": 8080, "auto": False},
           {"name": "[SSH] 공사망 매퍼 운영", "local_port": 18090, "remote_host": "172.17.155.132", "remote_port": 8090, "auto": True},
           {"name": "내 개인 터널", "local_port": 21521, "remote_host": "10.0.0.1", "remote_port": 1521, "auto": True}]
    keep = id(old[0])
    renamed, added, skipped = it.merge_std_tunnels(old)
    by = {(t["remote_host"], t["remote_port"]): t for t in old}
    check("반영: 옛 이름 → 표준 이름", by[("172.17.155.132", 5222)]["name"] == "[SSH] 공사망 매퍼배포"
          and by[("127.0.0.1", 8080)]["name"] == "[WEB] MIG 매퍼 및 에어플로 운영" and len(renamed) == 5, str(renamed))
    check("반영: 이름만 바꾸고 포트 · 자동 · 객체 유지", by[("172.17.155.132", 8090)]["local_port"] == 18090
          and by[("127.0.0.1", 8080)]["auto"] is False and id(old[0]) == keep)
    check("반영: 없는 표준 터널 추가(공사망 DB 외 자동 아님)",
          len(added) == 8 and all(not t["auto"] for t in old if t["name"] in added and "공사망 prd_fmsown" not in t["name"]),
          str(added))
    check("반영: 내 PC 포트 겹치면 건너뜀",
          [n for n, _ in skipped] == ["[DB] 개발망 org_ora ufmsadmin·ufmsfac·airphoto (Oracle)"], str(skipped))
    check("반영: 사용자 고유 터널은 그대로", by[("10.0.0.1", 1521)]["name"] == "내 개인 터널")
    check("반영: 두 번째는 바뀔 것 없음", it.merge_std_tunnels(old)[:2] == ([], []))


# ---------------------------------------------------------- config.ini 망별 표시 스위치 show_mig · show_dev · show_prd (2026-10-30c)
def test_net_ini():
    d = tempfile.mkdtemp()
    it.CONF_PATH = os.path.join(d, "ifms_tunnel.json")
    it.INI_PATH = os.path.join(d, "config.ini")
    write = lambda text, enc="utf-8": open(it.INI_PATH, "w", encoding=enc).write(text)  # noqa: E731
    nets = lambda c: sorted({it.tunnel_net(t) for t in c["tunnels"]})  # noqa: E731

    cfg = it.read_config()
    txt = open(it.INI_PATH, encoding="utf-8").read()
    check("ini: 없으면 기본 config.ini 생성(mig · dev true, prd false)", "show_mig = true" in txt
          and "show_dev = true" in txt and "show_prd = false" in txt and "show_gongsa" not in txt)
    check("ini 기본: MIG · 개발망 표시, 공사망 6건 숨김", cfg["show"] == {"mig": True, "dev": True, "prd": False}
          and nets(cfg) == ["dev", "mig"] and len(cfg["tunnels"]) == 8 and len(cfg["hidden_tunnels"]) == 6, str(cfg["show"]))
    cfg["tunnels"].append({"name": "[DB] 개발망 내 것", "local_port": 15000, "remote_host": "172.17.210.1", "remote_port": 1,
                           "auto": True})
    it.save_config(cfg)
    raw = json.load(open(it.CONF_PATH, encoding="utf-8"))
    check("ini: 저장하면 숨긴 터널도 그대로 남음", len(raw["tunnels"]) == 15 and "hidden_tunnels" not in raw and "show" not in raw)

    write("[tunnels]\nshow_mig = false\nshow_dev = false\nshow_prd = True\n", "utf-8-sig")   # 메모장 UTF-8(BOM)
    cfg = it.read_config()
    check("ini: prd 만 true → 공사망 6건만 표시", nets(cfg) == ["prd"] and len(cfg["tunnels"]) == 6
          and len(cfg["hidden_tunnels"]) == 9)
    write("[tunnels]\nshow_mig = true\nshow_dev = false\nshow_prd = false\n")
    cfg = it.read_config()
    check("ini: mig 만 true → MIG 1건만 표시", [t["local_port"] for t in cfg["tunnels"]] == [8080])

    write("; 예전 판\n[tunnels]\nshow_gongsa = true\n")
    cfg = it.read_config()
    txt = open(it.INI_PATH, encoding="utf-8").read()
    check("ini 예전 형식: show_gongsa = true → show_prd = true 로 읽고 새 형식으로 바꿔 씀",
          cfg["show"] == {"mig": True, "dev": True, "prd": True} and "show_prd = true" in txt and "show_gongsa" not in txt, txt[-80:])
    write("[tunnels]\nshow_mig = 예\nshow_prd = 아니오\n")   # 값 오류 · show_dev 누락
    check("ini 값 오류 · 누락: 기본값(mig · dev true, prd false)",
          it.read_config()["show"] == {"mig": True, "dev": True, "prd": False})

    net = lambda name, host: it.tunnel_net({"name": name, "remote_host": host})  # noqa: E731
    check("망 판별: 공사망 · 172.17.155.x → prd", net("운영망 매퍼배포", "172.17.155.132") == "prd"
          and net("[SSH] 공사망 자료적재", "10.0.0.1") == "prd")
    check("망 판별: MIG · 127.0.0.1 → mig, 개발망 · 기타 → dev",
          net("[WEB] MIG 매퍼", "127.0.0.1") == "mig" and net("내 터널", "127.0.0.1") == "mig"
          and net("[DB] 개발망 mig_fclown", "172.17.210.158") == "dev" and net("내 DB", "10.1.1.1") == "dev")


# ---------------------------------------------------------- 서비스 빨간불 재현: 사용자 known_hosts 에만 있는 호스트 키
def test_hostkey_sync(echo):
    """화면은 %USERPROFILE%\\.ssh\\known_hosts 의 키로 접속되지만 서비스(LocalSystem)는 그 파일을 못 본다."""
    import json
    srv = SshServer()
    name = f"[127.0.0.1]:{srv.port}"
    d = tempfile.mkdtemp()
    user_kh = os.path.join(d, "user_known_hosts")
    hk = paramiko.HostKeys()
    hk.add(name, HOSTKEY.get_name(), HOSTKEY)
    hk.save(user_kh)
    app_kh = os.path.join(d, "known_hosts")
    saved = it.KNOWN_HOSTS, it.USER_KNOWN_HOSTS
    it.KNOWN_HOSTS, it.USER_KNOWN_HOSTS = app_kh, user_kh
    server = {"host": "127.0.0.1", "port": srv.port, "user": "cloud", "auth": "password"}

    # (수정 전 동작) 화면 접속은 되지만 서비스용 파일에는 아무것도 남지 않음
    q, evs = queue.Queue(), []
    threading.Thread(target=drain, args=(q, evs), daemon=True).start()
    old = it.Engine(q, sync_known=False)
    old.server, old.password = server, "test"
    old._connect()
    old._drop()
    empty = not os.path.exists(app_kh) or not paramiko.HostKeys(app_kh).lookup(name)
    check("재현: 사용자 known_hosts 키로 화면 접속 시 서비스용 파일 비어 있음", empty)
    # 서비스(사용자 파일 못 봄)는 거부 → 빨간불
    it.USER_KNOWN_HOSTS = os.path.join(d, "systemprofile_known_hosts")
    evs2 = []
    r = it.Runner(lambda lv, m: evs2.append(m), mode="test")
    it.CONF_PATH = os.path.join(d, "ifms_tunnel.json")
    it.STATUS_PATH = os.path.join(d, "service_status.json")
    cfg = json.loads(json.dumps(it.DEFAULT_CONFIG))
    cfg["server"].update(host="127.0.0.1", port=srv.port, password_svc=it.protect_password("test", machine=True))
    lp = free_port()
    cfg["tunnels"] = [{"name": "E", "local_port": lp, "remote_host": "127.0.0.1", "remote_port": echo, "auto": True}]
    it.save_config(cfg)
    th = threading.Thread(target=r.run, daemon=True)
    th.start()

    def st():
        try:
            with open(it.STATUS_PATH, encoding="utf-8") as f:
                return json.load(f).get("ssh_state")
        except (OSError, ValueError):
            return None

    end = time.time() + 15
    while time.time() < end and "호스트 키" not in str(st()):
        time.sleep(0.3)
    check("재현: 서비스는 호스트 키 거부(빨간불)", "호스트 키" in str(st()), st())

    # (수정 후) 화면 접속(sync_known=True) → 서비스용 파일에 키 저장 → 서비스가 감지해 연결
    it.USER_KNOWN_HOSTS = user_kh
    new = it.Engine(queue.Queue(), sync_known=True)
    new.server, new.password = server, "test"
    new._connect()
    new._drop()
    check("수정: 화면 접속 시 확인된 키를 서비스용 known_hosts 에 저장", bool(paramiko.HostKeys(app_kh).lookup(name)))
    it.USER_KNOWN_HOSTS = os.path.join(d, "systemprofile_known_hosts")
    end = time.time() + 15
    while time.time() < end and st() != "연결됨":
        time.sleep(0.3)
    check("수정: 서비스가 키를 읽고 자동으로 연결", st() == "연결됨", st())
    check("수정: 서비스 터널 중계", roundtrip(lp, b"hk") == b"hk")
    r.stop()
    th.join(10)

    # 화면 시작 시 동기화(sync_known_hosts): 사용자 파일 → 서비스용 파일
    os.remove(app_kh)
    it.USER_KNOWN_HOSTS = user_kh
    res = it.sync_known_hosts("127.0.0.1", srv.port)
    check("수정: 사용자 known_hosts 키를 서비스용으로 복사", res and res[1] == user_kh
          and bool(paramiko.HostKeys(app_kh).lookup(name)))
    check("수정: 같은 키면 다시 쓰지 않음", it.sync_known_hosts("127.0.0.1", srv.port) is None)
    # 서버 키가 바뀐 경우(확인된 새 키로 교체)
    newkey = paramiko.RSAKey.generate(2048)
    res = it.sync_known_hosts("127.0.0.1", srv.port, newkey)
    check("수정: 확인된 새 키로 교체", res and paramiko.HostKeys(app_kh).lookup(name).get(newkey.get_name()) == newkey)
    srv.kill()
    it.KNOWN_HOSTS, it.USER_KNOWN_HOSTS = saved


def test_keepalive_no_reply():
    """keepalive(global request)에 응답하지 않는 서버 — 예전엔 95초마다 끊고 재연결했다."""
    srv = SshServer(no_global_reply=True)
    hk = paramiko.HostKeys()
    hk.add(f"[127.0.0.1]:{srv.port}", HOSTKEY.get_name(), HOSTKEY)
    hk.save(it.KNOWN_HOSTS)
    q, evs = queue.Queue(), []
    threading.Thread(target=drain, args=(q, evs), daemon=True).start()
    eng = it.Engine(q)
    eng.start({"host": "127.0.0.1", "port": srv.port, "user": "cloud", "auth": "password",
               "keepalive_interval": 5, "keepalive_count": 2}, "test")
    wait_state(eng, "연결됨")
    first = eng.connected_at
    time.sleep(25)       # 예전 판이면 5*2+5=15초에 끊김
    check("keepalive 무응답 서버에서도 연결 유지", eng.state == "연결됨" and eng.connected_at == first,
          f"{eng.state}")
    check("keepalive 무응답 안내 로그", any(e[0] == "log" and "TCP keepalive" in e[2] for e in evs))
    eng.stop()
    srv.kill()


# ---------------------------------------------------------- 서비스 본체(Runner)
def test_runner(echo):
    import json
    import subprocess
    d = tempfile.mkdtemp()
    it.CONF_PATH = os.path.join(d, "ifms_tunnel.json")
    it.KNOWN_HOSTS = os.path.join(d, "known_hosts")
    it.STATUS_PATH = os.path.join(d, "service_status.json")
    it.LOG_DIR = os.path.join(d, "logs")
    it.Runner.PORT_RETRY = 2
    srv = SshServer()
    hk = paramiko.HostKeys()
    hk.add(f"[127.0.0.1]:{srv.port}", HOSTKEY.get_name(), HOSTKEY)
    hk.save(it.KNOWN_HOSTS)
    kh_backup = open(it.KNOWN_HOSTS).read()
    pa, pb, pc, pd = free_port(), free_port(), free_port(), free_port()
    cfg = json.loads(json.dumps(it.DEFAULT_CONFIG))
    cfg["server"].update(host="127.0.0.1", port=srv.port, password_svc=it.protect_password("test", machine=True),
                         keepalive_interval=5, keepalive_count=2)
    cfg["tunnels"] = [
        {"name": "A", "local_port": pa, "remote_host": "127.0.0.1", "remote_port": echo, "auto": True},
        {"name": "B", "local_port": pb, "remote_host": "127.0.0.1", "remote_port": echo, "auto": True},
        {"name": "C", "local_port": pc, "remote_host": "127.0.0.1", "remote_port": echo, "auto": False},
    ]

    def save():
        it.save_config(cfg)
        time.sleep(0.05)

    def status():
        try:
            with open(it.STATUS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def until(cond, sec=15):
        end = time.time() + sec
        while time.time() < end:
            st = status()
            if st and cond(st):
                return st
            time.sleep(0.3)
        return status()

    def rows(st):
        return {r["local_port"]: r for r in st.get("tunnels", [])}

    logs = []
    save()
    r = it.Runner(lambda lv, m: logs.append((lv, m)), mode="test")
    th = threading.Thread(target=r.run, daemon=True)
    th.start()
    st = until(lambda s: s.get("ssh_state") == "연결됨" and len(s["tunnels"]) == 2
               and all(x["status"] == "열림" for x in s["tunnels"]))
    check("서비스: 연결 · 자동 터널 2건 열림", st.get("ssh_state") == "연결됨" and set(rows(st)) == {pa, pb}, st.get("ssh_state"))
    check("서비스: 자동 아닌 터널은 안 엶", it.port_free("127.0.0.1", pc))
    check("서비스: 중계", roundtrip(pa, b"svc") == b"svc")

    # 설정 변경 자동 반영: A 자동 해제, C 자동 추가
    cfg["tunnels"][0]["auto"] = False
    cfg["tunnels"][2]["auto"] = True
    save()
    st = until(lambda s: set(rows(s)) == {pb, pc} and rows(s)[pc]["status"] == "열림")
    check("서비스: 설정 변경 반영(추가 · 해제)", set(rows(st)) == {pb, pc} and it.port_free("127.0.0.1", pa))
    check("서비스: 변경 후 중계", roundtrip(pc, b"c") == b"c")
    check("서비스: 터널만 바뀌면 SSH 재접속 안 함", sum(1 for _, m in logs if "SSH 연결이 완료" in m) == 1)

    # 포트 점유 → 재시도로 열림
    hold = socket.socket()
    hold.bind(("127.0.0.1", pd))
    hold.listen()
    cfg["tunnels"].append({"name": "D", "local_port": pd, "remote_host": "127.0.0.1", "remote_port": echo, "auto": True})
    save()
    st = until(lambda s: rows(s).get(pd, {}).get("status") == "포트 사용중")
    check("서비스: 포트 사용중 표시", rows(st).get(pd, {}).get("status") == "포트 사용중", rows(st).get(pd, {}).get("msg"))
    hold.close()
    st = until(lambda s: rows(s).get(pd, {}).get("status") == "열림", 10)
    check("서비스: 포트 풀리면 자동으로 열림", rows(st).get(pd, {}).get("status") == "열림")

    # 비밀번호 틀림 → 인증 실패, 재시도 억제 → 고치면 바로 재연결
    cfg["server"]["password_svc"] = it.protect_password("wrong", machine=True)
    save()
    st = until(lambda s: s.get("ssh_state") == "인증 실패")
    check("서비스: 인증 실패 상태", st.get("ssh_state") == "인증 실패", st.get("ssh_state"))
    n = sum(1 for _, m in logs if "접속을 시도" in m)
    time.sleep(4)
    check("서비스: 인증 실패 후 재시도 억제", sum(1 for _, m in logs if "접속을 시도" in m) == n)
    check("서비스: 포트는 계속 열려 있음", rows(status()).get(pb, {}).get("status", "").startswith("열림"))
    cfg["server"]["password_svc"] = it.protect_password("test", machine=True)
    save()
    st = until(lambda s: s.get("ssh_state") == "연결됨")
    check("서비스: 비밀번호 고치면 재연결", st.get("ssh_state") == "연결됨")

    # 모르는 호스트 키 → 등록하지 않고 대기 → known_hosts 복구 시 연결
    os.remove(it.KNOWN_HOSTS)
    st = until(lambda s: "거부" in str(s.get("ssh_state")))
    check("서비스: 모르는 호스트 키 거부", "거부" in str(st.get("ssh_state")), st.get("ssh_state"))
    check("서비스: 호스트 키 자동 등록 안 함", not os.path.exists(it.KNOWN_HOSTS) or os.path.getsize(it.KNOWN_HOSTS) == 0)
    with open(it.KNOWN_HOSTS, "w") as f:
        f.write(kh_backup)
    st = until(lambda s: s.get("ssh_state") == "연결됨")
    check("서비스: 호스트 키 등록 후 연결", st.get("ssh_state") == "연결됨")

    # 서버 재시작 → 자동 재연결
    port = srv.port
    srv.kill()
    until(lambda s: s.get("ssh_state") != "연결됨", 15)
    srv = SshServer(port)
    st = until(lambda s: s.get("ssh_state") == "연결됨", 20)
    check("서비스: 서버 재시작 후 재연결", st.get("ssh_state") == "연결됨")
    check("서비스: 재연결 후 중계", roundtrip(pb, b"b") == b"b")

    # 중지
    r.stop()
    th.join(10)
    st = status()
    check("서비스: 중지 시 상태 stopped · 포트 해제",
          st.get("stopped") and all(it.port_free("127.0.0.1", p) for p in (pb, pc, pd)))
    check("서비스: 비밀번호 로그 미노출", not any("test" == m or "wrong" in m for _, m in logs))
    srv.kill()

    # 서비스 관리자 밖에서 --service-run → 오류 코드로 끝남(1063)
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "run.py")
    rc = subprocess.run([sys.executable, src, "--service-run"], capture_output=True, timeout=60).returncode
    check("서비스: SCM 밖 직접 실행은 실패 코드", rc == 1, f"rc={rc}")
    state, info = it.svc_query()
    check("서비스 조회(설치 안 됨 = None)", state in (None, 1, 4), f"{state} {info}")


if __name__ == "__main__":
    main()
