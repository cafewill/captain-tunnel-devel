"""JSON 스키마의 실행 전 검증. 숨겨진 터널도 동일하게 검증한다."""


def _port(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{label}: 정수 포트가 필요합니다.")
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{label}: 정수 포트가 필요합니다.") from None
    if not 1 <= number <= 65535:
        raise ValueError(f"{label}: 포트는 1~65535 범위여야 합니다.")
    return number


def validate_config(cfg):
    if not isinstance(cfg, dict) or not isinstance(cfg.get("server"), dict):
        raise ValueError("server는 객체여야 합니다.")
    server = cfg["server"]
    for key in ("host", "user"):
        if not isinstance(server.get(key), str) or not server[key].strip():
            raise ValueError(f"server.{key}: 빈 문자열은 사용할 수 없습니다.")
    server["port"] = _port(server.get("port"), "server.port")
    if server.get("auth") not in ("password", "key"):
        raise ValueError("server.auth: password 또는 key를 지정하십시오.")
    for key in ("key_file", "password_dpapi", "password_svc"):
        if key in server and not isinstance(server[key], str):
            raise ValueError(f"server.{key}: 문자열이 필요합니다.")
    for key in ("keepalive_interval", "keepalive_count"):
        value = server.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"server.{key}: 양의 정수가 필요합니다.")
    tunnels = cfg.get("tunnels")
    if not isinstance(tunnels, list):
        raise ValueError("tunnels는 배열이어야 합니다.")
    ports = set()
    for index, tunnel in enumerate(tunnels, 1):
        if not isinstance(tunnel, dict):
            raise ValueError(f"터널 {index}: 객체가 필요합니다.")
        for key in ("name", "remote_host"):
            if not isinstance(tunnel.get(key), str) or not tunnel[key].strip():
                raise ValueError(f"터널 {index}.{key}: 빈 문자열은 사용할 수 없습니다.")
        tunnel.setdefault("bind", "127.0.0.1")
        tunnel.setdefault("auto", True)
        if not isinstance(tunnel["bind"], str) or not tunnel["bind"].strip():
            raise ValueError(f"터널 {index}.bind: 주소가 필요합니다.")
        if not isinstance(tunnel["auto"], bool):
            raise ValueError(f"터널 {index}.auto: true 또는 false가 필요합니다.")
        for key in ("local_port", "remote_port"):
            tunnel[key] = _port(tunnel.get(key), f"터널 {index}.{key}")
        if tunnel["local_port"] in ports:
            raise ValueError(f"내 PC 포트 {tunnel['local_port']}가 중복되었습니다.")
        ports.add(tunnel["local_port"])
    return cfg
