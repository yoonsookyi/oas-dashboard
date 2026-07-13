# OAS Admin Lite

OAS Admin Lite는 Oracle Analytics Server(OAS) 운영자가 필요한 시점에 실행하는 경량 관리자 웹앱입니다. OAS의 상태, 패치, Catalog 객체, 제한된 운영 스크립트와 작업 이력을 한 화면에서 확인합니다.

앱은 OAS 설치 계정인 `oracle`로 실행하며, systemd 서비스·sudoers·외부 Python 패키지·별도 DB를 추가하지 않습니다. 앱 데이터와 로그는 기본적으로 `/u01/oas-admin-lite` 아래에 저장됩니다.

## 문서 흐름

이 문서는 다음 순서로 구성됩니다.

1. [아키텍처](#아키텍처)와 보안 경계
2. [기능](#기능)과 수집 범위
3. [설치 및 배포](#설치-및-배포)
4. [운영 및 사용 방법](#운영-및-사용-방법)
5. [업데이트·롤백·제거](#업데이트-롤백-및-제거)

현재처럼 OAS와 OHS가 하나의 VM에 설치되고 도메인·SSL이 없는 환경의 상세 절차와 배포 파일 목록은 [단일 VM 배포 가이드](docs/DEPLOYMENT_TOPOLOGY.md)를 참고하세요.

## 아키텍처

### 현재 단일 VM 환경

```text
관리자 PC -- SSH tunnel --> Admin Lite (127.0.0.1:18080)
                                      │ HTTP loopback
                                      ▼
                             OHS (127.0.0.1:7777)
                                      ▼
                                     OAS
                                      ▼
                                     DB
```

- Admin Lite는 OAS/OHS가 설치된 VM에서 `oracle` 계정으로 실행합니다.
- 관리 웹앱은 `127.0.0.1:18080`에서만 수신합니다. 관리자는 SSH tunnel을 통해 접속합니다.
- Catalog는 OHS가 제공하는 OAS Catalog REST API를 호출합니다.
- 현재 SSL과 도메인이 없으므로 Admin Lite와 OHS 사이에는 같은 VM 내부의 HTTP loopback 통신을 사용합니다. `18080`은 외부 방화벽에 열지 않습니다.

### 분리 서버 환경

OAS·OHS·DB가 분리된 환경에서도 동작합니다. Admin Lite는 OAS VM에 두고, `catalog_base_url`에는 OAS VM에서 접근 가능한 내부 OHS 또는 내부 LB 주소를 지정합니다. OHS가 다른 VM이면 `ohs.monitor_local: false`로 설정하여 OHS의 로컬 파일·프로세스 점검을 건너뜁니다. DB는 OAS가 연결하는 데이터 계층이며 Admin Lite가 DB OS나 테이블을 직접 조회하지 않습니다.

## 기능

| 화면 | 기능 |
|---|---|
| Resources | CPU, 메모리, Swap, 디스크, OAS/OHS 경로, listener, 프로세스 상태 확인 |
| Catalog | OAS Catalog REST API로 Classic과 DV Catalog 객체를 수집하고 owner·변경일·폴더·ACL 위험을 요약 |
| Patch | `opatch lsinventory` 기반 패치 수준 조회 |
| Scripts | `diagnostic_dump.sh`, `exportarchive.sh`의 명령 미리보기와 제한된 실행 |
| Jobs / Audit | 실행 결과, stdout/stderr, 종료 코드, 로그 경로 이력 |
| Settings | 현재 적용된 앱·OAS·보안 설정 확인 |

### Catalog 수집 범위

Catalog 수집은 **OAS Catalog REST API만** 사용합니다. 실행 시 `/catalog`가 반환하는 지원 type 목록을 확인한 뒤, 제공되는 type을 순회합니다.

- Classic: `analysis`, `dashboards`, `dashboardpages`, `reports`
- DV 및 데이터 자산: `workbooks`, `datasets`, `connections`, `dataflows`, `models`, `sequences`

`runcat.sh`와 Catalog DB 직접 조회는 웹앱의 수집 경로에 사용하지 않습니다. REST API는 Web tier를 통해 접근해야 하므로, OAS 관리 포트나 내부 listener를 새로운 REST 공개 경로로 사용하지 마세요. [OAS Catalog REST API](https://docs.oracle.com/en/middleware/bi/analytics-server/oasri/api-catalog.html)

## 요구 사항과 운영 정책

운영 VM에는 다음만 필요합니다.

```text
Linux, python3 3.6 이상, bash, tar, gzip
oracle 계정
OAS / OHS / FMW / OPatch가 설치·기동된 환경
```

`pip`, Node.js, 외부 DB, systemd, sudoers, 별도 nginx/apache는 필요하지 않습니다.

앱은 임의 shell 명령을 제공하지 않습니다. Scripts 화면에서 실행 가능한 OAS 스크립트는 다음 두 개로 고정됩니다.

- `diagnostic_dump.sh`
- `exportarchive.sh`

## 설치 및 배포

### 1. Git clone 설치

OAS VM에서 `oracle` 계정으로 실행합니다.

```bash
su - oracle
cd /u01
git clone <REPOSITORY_URL> oas-admin-lite
cd /u01/oas-admin-lite
chmod +x scripts/*.sh
./scripts/install.sh /u01/oas-admin-lite
```

`install.sh`는 운영 디렉터리를 만들고 `app/config/app.yaml`이 없으면 설정 샘플을 복사합니다. 기존 `app.yaml`은 덮어쓰지 않습니다.

### 2. 설정

현재 단일 VM 환경의 최소 설정 예시입니다. 경로는 실제 설치 경로로 바꾸세요.

```yaml
server:
  listen: "127.0.0.1:18080"

oas:
  oracle_home: "/u01/app/Oracle/Middleware/Oracle_Home"
  domain_home: "/u01/data/domains/bi"
  bitools_bin: "/u01/data/domains/bi/bitools/bin"
  catalog_base_url: "http://127.0.0.1:7777"
  catalog_api_path: "/api/20210901/catalog"
  catalog_username: "<CATALOG_READ_USER>"

ohs:
  monitor_local: true
  oracle_home: "<OHS_ORACLE_HOME>"
  domain_home: "<OHS_DOMAIN_HOME>"
  http_port: "7777"
  https_port: ""

scripts:
  allowed:
    - "diagnostic_dump.sh"
    - "exportarchive.sh"
```

Catalog 계정의 비밀번호는 설정 파일 대신 시작 전 환경변수로 전달합니다.

```bash
export OAS_ADMIN_LITE_CATALOG_USERNAME="<CATALOG_READ_USER>"
export OAS_ADMIN_LITE_CATALOG_PASSWORD="<PASSWORD>"
```

선택적으로 웹앱 Basic Auth를 사용하려면 `security.password_sha256` 또는 `OAS_ADMIN_LITE_PASSWORD_SHA256`를 설정합니다.

### 3. 사전 점검 및 시작

```bash
cd /u01/oas-admin-lite
./scripts/healthcheck.sh
./scripts/start.sh
./scripts/status.sh
```

`healthcheck.sh`는 Python 실행 환경, 앱 데이터 디렉터리 권한, OAS 경로, OPatch, 허용 스크립트, 선택한 OHS 점검 및 Catalog REST 네트워크 연결을 확인합니다. `18080`이 이미 사용 중이라는 경고는 실행 중인 Admin Lite가 해당 포트를 사용하고 있을 수 있으므로 `./scripts/status.sh`와 `ss -lntp | grep ':18080'`으로 확인하세요.

## 운영 및 사용 방법

### 관리자 접속

관리자 PC에서 SSH tunnel을 엽니다.

```bash
ssh -i <PRIVATE_KEY_FILE> -N -L 18080:127.0.0.1:18080 oracle@<OAS_VM_IP>
```

예를 들어 Windows PowerShell에서는 다음과 같이 실행합니다.

```powershell
ssh -i "C:\Keys\oas-admin-lite.pem" -N -L 18080:127.0.0.1:18080 oracle@<OAS_VM_IP>
```

개인키 파일은 관리자 PC에만 보관하고 Git 저장소·운영 서버·앱 설정 파일에는 저장하지 않습니다.

브라우저에서 아래 주소로 접속합니다.

```text
http://127.0.0.1:18080
```

### 화면별 사용 순서

1. **Resources**에서 OAS 경로, OPatch, OHS listener와 프로세스 상태를 확인합니다.
2. **Catalog**에서 수집을 실행합니다. Classic과 DV 객체 type·건수가 표시되면 REST endpoint와 인증이 정상입니다.
3. **Patch**에서 현재 적용된 OPatch inventory를 확인합니다.
4. **Scripts**에서 명령을 먼저 미리보기한 뒤 필요한 경우에만 실행합니다.
5. **Jobs / Audit**에서 결과와 로그 경로를 확인합니다.

Catalog 화면에서 JSON 대신 HTML이 표시되면 REST endpoint가 아닌 로그인·화면 URL을 가리키는 경우가 많습니다. `catalog_base_url`, `catalog_api_path`, 인증 정보를 확인하세요.

실행 로그는 다음 파일에서 확인합니다.

```bash
tail -n 100 /u01/oas-admin-lite/logs/app.log
```

중지와 상태 확인은 다음 명령을 사용합니다.

```bash
./scripts/stop.sh
./scripts/status.sh
```

## 업데이트, 롤백 및 제거

### Git 업데이트

```bash
cd /u01/oas-admin-lite
./scripts/stop.sh
git pull --ff-only
./scripts/healthcheck.sh
./scripts/start.sh
```

### 릴리스 패키지 업데이트

Git 접근이 불가능한 환경에서는 빌드 서버에서 패키지를 생성합니다.

```bash
./scripts/package.sh 0.1.0
```

생성된 `dist/oas-admin-lite-0.1.0.tar.gz`를 운영 서버로 복사한 뒤 실행합니다.

```bash
./scripts/update.sh /u01/oas-admin-lite/packages/releases/oas-admin-lite-0.1.0.tar.gz
./scripts/healthcheck.sh
./scripts/start.sh
```

업데이트 전 앱 코드의 백업은 `packages/rollback/`에 생성됩니다. 롤백은 다음과 같이 실행합니다.

```bash
./scripts/rollback.sh
```

앱만 제거하고 운영 데이터는 유지하려면 다음을 실행합니다.

```bash
./scripts/uninstall.sh
```

`KEEP_DATA=0`을 지정하면 앱 데이터·로그·백업도 함께 제거합니다.

## 개발 및 검증

개발 환경에서는 다음을 실행합니다.

```bash
python3 -m unittest discover -s tests
python3 -m compileall app tests
```

OAS 관련 확장은 OBIEE 문서가 아닌 Oracle Analytics Server 문서를 기준으로 검토합니다. 특히 [OAS 서비스 인스턴스 관리 스크립트](https://docs.oracle.com/en/middleware/bi/analytics-server/administer-oas/scripts-managing-service-instances.html)와 [Catalog REST API](https://docs.oracle.com/en/middleware/bi/analytics-server/oasri/api-catalog.html)를 참고하세요.
