# 캡틴 터널링

Windows에서 여러 SSH 로컬 포워딩을 하나의 경유 SSH 연결로 관리하는 데스크톱 도구입니다. 터널별 시작·중지, 자동 재연결, 연결 수·송수신량, 포트 점유 확인, Windows 서비스 실행을 지원합니다.

`legacy/20261030-IFMS터널링유틸`의 `2026-10-30c` 소스를 바탕으로 루트 프로젝트를 재구성했습니다. 원본은 보존하며 새 프로그램은 `legacy/` 없이 실행·빌드할 수 있습니다. 기본 IFMS 서버·터널 목록과 망별 표시 기능은 유지합니다.

## 빠른 시작

Windows, Python 3.12 이상과 Tkinter가 필요합니다. 저장소 루트에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
.\.venv\Scripts\python.exe run.py
```

설치 후 `captain-tunnel` 또는 콘솔 창 없는 `captain-tunnel-gui` 명령도 사용할 수 있습니다.

```powershell
.\.venv\Scripts\captain-tunnel.exe --help
.\.venv\Scripts\captain-tunnel.exe --version
.\.venv\Scripts\captain-tunnel.exe --headless
```

처음에는 경유 서버 주소·계정을 확인하고, 서버 담당자가 제공한 호스트 키 지문을 대조한 다음 터널을 시작합니다. 대상 애플리케이션은 `127.0.0.1:<내 PC 포트>`에 연결합니다.

## 저장 위치

| 실행 방식 | 설정·로그 위치 |
|---|---|
| 소스 실행 | 프로젝트 `data/` |
| 배포 EXE | `CaptainTunnel.exe` 옆 |
| 일반 패키지 설치(소스 체크아웃 없음) | `%LOCALAPPDATA%\Captain-Tunnel` |
| 위 위치에 쓰기 불가 | `%LOCALAPPDATA%\Captain-Tunnel` |

설정 이름은 `captain_tunnel.json`입니다. `known_hosts`, `config.ini`, `service_status.json`, `logs/`도 같은 데이터 폴더를 사용합니다. 실행 데이터는 Git과 배포 묶음에서 제외합니다. 구체적인 설정 예제는 [config/captain_tunnel.example.json](config/captain_tunnel.example.json)에 있습니다.

## 시험과 빌드

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\python.exe tests\integration_engine.py
.\.venv\Scripts\python.exe tools\build.py
```

통합 시험은 로컬 SSH·에코 서버를 띄우며 재연결 대기 때문에 수 분이 걸립니다. 외부 IFMS 서버에 접속하지 않습니다. 빌드 결과는 `dist/<빌드시각>/CaptainTunnel.exe`입니다. Windows 서비스는 빌드된 EXE에서 설치합니다.

## 문서

| 문서 | 내용 |
|---|---|
| [AGENTS.md](AGENTS.md) | 협업·변경·검증 규칙 |
| [GLANCE.md](GLANCE.md) | 구조·실행 식별자·현재 상태 |
| [docs/docs.md](docs/docs.md) | 문서 색인 |
| [docs/user-guide.md](docs/user-guide.md) | 사용·자동 실행·장애 대응 |
| [docs/development.md](docs/development.md) | 개발·시험·빌드 |
| [docs/architecture.md](docs/architecture.md) | 연결·서비스·설정 구조 |
| [docs/migration.md](docs/migration.md) | 기존 IFMS 판에서 이관 |
| [docs/review.md](docs/review.md) | 원본 리뷰·수정·검증 한계 |

브라우저에서는 [가이드 목차 HTML](docs/docs.html)을 엽니다. 모든 가이드는 `.md`·`.html` 쌍으로 제공하며, 우측 상단 설정에서 화면 모드·스킨·글자 크기를 바꿀 수 있습니다. 재생성 방법과 보기 설정 표준은 [GLANCE.md](GLANCE.md)를 따릅니다.

현재 환경의 검증 결과와 미확인 사항은 [리뷰 기록](docs/review.md)에 명시합니다.
