# 개발·검증·배포

## 개발 환경

Windows와 Python 3.12 이상을 사용합니다. Tkinter가 포함된 Python 배포판이 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
.\.venv\Scripts\python.exe run.py --version
.\.venv\Scripts\python.exe run.py
```

네트워크가 없는 배포 PC에서는 의존성을 설치하지 않고 빌드된 EXE를 사용합니다. 소스 실행 데이터는 `data/`에 생깁니다. `application.py`를 파일 경로로 직접 실행하지 말고 `run.py` 또는 설치된 명령을 사용합니다.

## 검증 순서

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m compileall -q src tests tools run.py
python -m unittest discover -s tests -p "test_*.py" -v
python tests\integration_engine.py
```

| 시험 | 목적 |
|---|---|
| `test_configuration.py` | 잘못된 포트·구조·중복·keepalive 값의 실행 전 거부 |
| `test_lifecycle.py` | 중지·설정 교체 중 늦은 연결 성공/실패, 이전 keepalive, 늦은 채널 정리 |
| `test_cli.py` | 도움말·버전·잘못된 인수가 GUI를 만들지 않는지 |
| `integration_engine.py` | 기존 SSH 중계·대용량·동시 접속·재연결·DPAPI·Runner·표시 설정 회귀 |

통합 시험은 루프백에 임시 SSH 서버를 띄우며 기존 시험의 대기 구간을 포함합니다. Windows에서 실행하고 결과를 끝까지 확인합니다. 실제 Windows 서비스 설치나 재부팅을 수행하는 시험은 아닙니다. Python을 사용할 수 없으면 스크립트 실행이 허용된 환경에서 `powershell -NoProfile -File tools/check_structure.ps1`로 구조·이관 동등성만 검사할 수 있습니다. 실행 정책이 차단하면 정책을 변경하지 않고 필요한 항목을 읽기 전용 명령으로 확인합니다. 구조 검사를 Python 실행 시험 통과로 대체해서 기록하지 않습니다.

## 빌드

```powershell
python tools\build.py
```

빌드 도구는 패키지와 진입점만 로컬 임시 폴더에 복사하여 PyInstaller onefile로 묶습니다. 기존 배포 폴더는 수정하지 않고 `dist/<시각>/`에 새 산출물을 만듭니다. Python·Tkinter·Paramiko·pywin32가 EXE 안에 포함됩니다. 아이콘은 기존 일반 터널 아이콘을 사용합니다.

운영 `captain_tunnel.json`, `known_hosts`, `service_status.json`, 로그는 묶지 않습니다. 설정 예제와 문서만 함께 배포합니다. 빌드 출력 폴더를 실제 운영 위치로 옮긴 다음 서비스로 설치합니다.

## 가이드 문서 관리

`docs/*.md`를 고치면 같은 이름의 `.html`을 함께 재생성합니다. 템플릿과 화면 설정의 정본은 [GLANCE.md](../GLANCE.md)의 「가이드 HTML 표준」입니다.

```powershell
python docs\tools\build_docs.py
python docs\tools\build_docs.py --check
```

생성기는 Python 표준 라이브러리만 사용합니다. HTML은 네트워크 없이 단독으로 열리며 Markdown 본문을 내장한 자바스크립트 렌더러가 제목·표·목록·코드를 표시합니다. 외부 CDN을 사용하지 않습니다. 배포 빌드는 문서 동기화 검사를 먼저 수행합니다.

문서 동기화 시험과 브라우저 검사는 다음과 같이 실행합니다. 브라우저 경로는 설치된 Chrome 또는 Edge에 맞춥니다.

```powershell
python -m unittest tests.test_docs -v
python tools\check_guides.py --browser "C:\Program Files\Google\Chrome\Application\chrome.exe"
python tools\check_guides.py --browser "C:\Program Files\Google\Chrome\Application\chrome.exe" --width 390
```

브라우저 검사는 탭·주소 조각·설정 열기/닫기·모드·스킨·글자 크기 범위·저장·초기화·인쇄 버튼·모바일 가로 넘침을 확인합니다. 브라우저가 실행되지 않으면 검증 실패로 기록하고 보안 설정을 해제하지 않습니다.

## 배포 PC 수동 검증

- GUI 최초 실행·아이콘·메뉴·버튼·고DPI 표시
- 비밀번호/키 인증과 서버 호스트 키 대조
- 로컬 포트 중계·연결 종료·재연결
- 서비스 설치·시작·중지·제거와 로그
- GUI 종료 후 터널 유지 및 재부팅 후 접속
- 예전 `IFMSTunnel` 서비스·자동 실행과의 포트 충돌 없음

서비스 설치·운영 접속은 환경별로 명시적으로 수행하고 결과를 기록합니다.
