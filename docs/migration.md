# 기존 IFMS 판에서 이관

## 달라진 식별자

| 항목 | 기존 | 캡틴 터널링 |
|---|---|---|
| 제품명 | IFMS 터널 | 캡틴 터널링 |
| 실행 파일 | `IFMS_Tunnel.exe` | `CaptainTunnel.exe` |
| 서비스 | `IFMSTunnel` | `CaptainTunnel` |
| 로그인 Run 값 | `IFMS_Tunnel` | `CaptainTunnel` |
| GUI 뮤텍스 | `Local\IFMS_Tunnel_SingleInstance` | `Local\CaptainTunnel_SingleInstance` |
| 설정 | `ifms_tunnel.json` | `captain_tunnel.json` |
| 사용자별 대체 폴더 | `%LOCALAPPDATA%\IFMS-Tunnel` | `%LOCALAPPDATA%\Captain-Tunnel` |
| 소스 실행 데이터 | 원본 소스 옆 | 새 프로젝트 `data/` |

프로그램은 기존 서비스·Run 값·설정 파일을 자동 수정하지 않습니다. 두 제품을 함께 켜면 동일한 로컬 포트는 충돌합니다.

## 설정 이관

1. 기존 화면에서 열린 터널을 닫고, 서비스가 실행 중이면 중지합니다. 기존 로그인 자동 실행도 해제합니다.
2. 새 프로그램을 실행하고 종료하여 데이터 폴더를 확인합니다. 이미 새 설정이 있으면 먼저 별도 보관합니다.
3. 기존 `ifms_tunnel.json`을 새 데이터 폴더에 **`captain_tunnel.json`이라는 이름으로** 복사합니다. 새 설정을 무심코 덮어쓰지 않습니다.
4. 기존 서버 키를 신뢰하는 근거를 확인하고 `known_hosts`를 옮깁니다. 망별 표시를 유지하려면 `config.ini`도 복사합니다.
5. 새 GUI를 열고 서버·인증·터널·숨김 설정을 확인한 다음 실제 연결을 시험합니다.
6. 필요하면 캡틴 터널링의 새 서비스를 설치합니다. 기존 IFMS 서비스 제거는 전환이 끝난 뒤 별도로 수행합니다.

상태 파일과 로그는 이관하지 않습니다. 서비스 이름이 달라 서비스 등록 자체를 옮길 수는 없습니다. 새 EXE의 설치 기능을 사용합니다.

## 암호와 키 파일

같은 Windows 계정·PC라면 기존 사용자 범위 DPAPI 암호를 사용할 수 있습니다. PC 범위 암호는 같은 PC에서만 사용합니다. 다른 계정·PC로 옮길 때는 비밀번호를 새로 입력하고 서비스용 암호도 다시 저장합니다. 키 파일 경로와 LocalSystem의 읽기 권한도 확인합니다.

원본 `legacy/`는 리뷰 근거로 남습니다. 새 프로젝트는 원본의 실행 파일·로그·개인 설정을 배포물에 포함하지 않습니다.
