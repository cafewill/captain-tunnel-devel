# 캡틴 터널링 프로젝트 현황

## 프로젝트 개요

| 항목 | 값 |
|---|---|
| 제품명 | 캡틴 터널링 |
| 패키지 / 버전 | `captain_tunnel` / `0.1.0` |
| 대상 | Windows 데스크톱·Windows 서비스 |
| 원본 | `legacy/20261030-IFMS터널링유틸/src`, `2026-10-30c` |
| 기본 기능 | 단일 SSH 다중 로컬 포워딩·재연결·DPAPI·서비스·망별 표시 |
| 실행 | `python run.py`, `captain-tunnel`, `captain-tunnel-gui` |
| 배포 | `CaptainTunnel.exe` |
| 서비스 / 로그인 자동 실행 | `CaptainTunnel` / `CaptainTunnel` |
| GUI 단일 실행 뮤텍스 | `Local\CaptainTunnel_SingleInstance` |

## 디렉터리

```text
captain-tunnel-devel/
  AGENTS.md                   협업·검증 규칙
  GLANCE.md                   현재 상태와 문서 색인
  README.md                   설치와 빠른 시작
  pyproject.toml              패키지·의존성·실행 명령
  requirements.txt            패키지 설치 대체용 의존성
  run.py                      소스 실행 진입점
  src/captain_tunnel/
    __init__.py               단일 버전 정의
    __main__.py               python -m captain_tunnel
    cli.py                    인수·플랫폼 확인
    paths.py                  소스·EXE 데이터 경로
    configuration.py          설정 구조·포트·중복 검증
    application.py            GUI·Engine·Forwarder·Runner·Windows 서비스
    icons_data.py             기존 내장 버튼 아이콘
    assets/captain_tunnel.ico  애플리케이션 아이콘
  config/                     비밀 없는 설정 예제
  tests/                      단위·회귀 시험, 기존 통합 시험 이관본
  tools/                      빌드·정적 구조 점검
  docs/                       MD·HTML 사용·개발·아키텍처·이관·리뷰
    tools/                    독립 HTML 템플릿·생성기
  data/                       소스 실행 데이터, Git 제외
  dist/                       시각별 배포 산출물, Git 제외
  legacy/                     읽기 전용 반입 원본, Git 제외
```

## 현황 — 2026-11-01

- 원본을 보존하고 루트 프로젝트를 패키지 구조로 재구성했습니다.
- 화면·서비스·로그인 자동 실행·뮤텍스·설정 파일의 제품 식별자를 분리했습니다.
- 기존 IFMS 서버와 표준 터널 목록을 유지했습니다. 공사망은 `config.ini`의 기존 기본값대로 숨깁니다.
- 취소된 연결 결과 폐기, 이전 연결 keepalive 결과 격리, 중지 후 늦게 열린 채널 정리, 설정 검증을 추가했습니다.
- 실행 시험·배포 EXE·서비스 설치·재부팅 검증 상태는 `docs/review.md`가 정본입니다.
- 정적 검증: 필수 파일 27개·문서 링크 25개·설정 예제·원본 목록·아이콘·제품 식별자·Git 제외 규칙을 확인했습니다. Python 실행 시험과 EXE 빌드는 환경 제약으로 미실행입니다.
- 기존 GUI·엔진·서비스 구현은 큰 단일 파일입니다. 재구성 단계에서 동작 보존을 우선했고 추가 분리는 후속 과제입니다.

## 문서 색인

| 문서 | 갱신 시점 |
|---|---|
| [docs/docs.md](docs/docs.md) | 문서 추가·이름 변경 |
| [docs/user-guide.md](docs/user-guide.md) | 화면·사용·서비스 동작 변경 |
| [docs/development.md](docs/development.md) | 설치·시험·빌드 변경 |
| [docs/architecture.md](docs/architecture.md) | 연결·저장·스레드 구조 변경 |
| [docs/migration.md](docs/migration.md) | 식별자·설정 호환 정책 변경 |
| [docs/review.md](docs/review.md) | 문제 수정·시험 결과·미검증 상태 변경 |

각 문서는 같은 이름의 `.html`을 함께 제공합니다. 브라우저 진입점은 [docs/docs.html](docs/docs.html)입니다.

## 가이드 HTML 표준

IFMS·슈퍼브챗의 `GLANCE.md` 및 `docs/tools/md_to_html.py`에서 기존 화면·보기 설정 방식을 확인하고 적용했습니다. 다른 프로젝트 파일을 복사해 실행하거나 런타임에서 참조하지 않습니다.

| 항목 | 기준 |
|---|---|
| 정본·생성본 | `docs/*.md`가 정본, 같은 이름의 `.html`은 생성본 |
| 재생성·점검 | `python docs/tools/build_docs.py`, `python docs/tools/build_docs.py --check` |
| 구성 | sticky 헤더·`##` 섹션 탭·히어로·본문·`###`가 2개 이상이면 On this page |
| 우측 상단 | `[📚 목차] [🖨 PDF] [⚙️ 설정]` 순서 |
| 화면 모드 | 주간·야간, 미선택 시 시스템 모드. 스위치 트랙 28×14px·노브 18px |
| 컬러 스킨 | 기본 그린 `#6db33f`·봄 `#e0607e`·여름 `#1e9bd7`·가을 `#d9731f`·겨울 `#4a5fc1` |
| 글자 크기 | 12~20px, 기본 15px, 본문·표·코드에 배율 적용 |
| 저장·초기화 | `captain-tunnel-guide-theme/skin/font`의 독립 localStorage 키. 기본값으로 버튼 제공 |
| 접근성 | 탭 키보드 이동·설정 aria-expanded·라디오 그룹·글자 값 aria-live·Esc/바깥 클릭 닫기·포커스 복귀 |
| 레이아웃 | 전체 폭, 여백 clamp(32px, 3vw, 64px). 1024px 사이드바 칩·900px 탭 둘째 줄·640px 터치 타깃 40px |
| 코드·인쇄 | 코드 가로 스크롤·복사. PDF는 모든 섹션을 주간 색으로 인쇄 |
| 오프라인 | 외부 CSS·JS·폰트 요청 없음. MD 본문을 HTML에 내장하고 브라우저에서 렌더링 |
| 지원 문법 | 제목·문단·표·번호/불릿 목록·코드펜스·인라인코드·굵게·링크·수평선·단순 인용 |

HTML에는 원문·템플릿 해시가 포함됩니다. Python 생성기는 출력 전체를 재계산해 변경 없이 동기화 여부를 검사합니다. 현재 가이드에 없는 중첩 목록·각주·복합 표 문법을 추가하려면 렌더러도 함께 확장합니다.

가이드 6쌍의 내장 원문·보기 설정 요소와 문서 링크 29개를 정적으로 확인했습니다. 현재 환경의 Chrome GPU 오류로 시각·상호작용 검증은 완료하지 못했습니다. 상세 상태는 `docs/review.md`를 확인합니다.

확인한 결과만 상태에 반영합니다. 이번 작업에서 커밋·푸시·서비스 설치는 수행하지 않습니다.
