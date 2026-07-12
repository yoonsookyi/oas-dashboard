# 분리 서버 배포와 검증

## 단일 VM 배포: OAS와 OHS가 같은 서버인 현재 환경

도메인과 SSL을 아직 구성하지 않은 1차 환경에서는 Admin Lite를 OAS/OHS가 설치된 Linux VM에 배포한다. Admin Lite는 외부에 공개하지 않고 loopback 주소(`127.0.0.1`)에서만 수신한다. OHS는 Admin Lite의 외부 프록시가 아니라, Admin Lite가 Catalog REST API를 호출할 때 사용하는 로컬 OAS Web tier다.

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

### 1. 사전 조건

- OAS와 OHS가 정상 기동되어 있어야 한다.
- OAS 설치 계정(`oracle`)으로 로그인할 수 있어야 한다.
- `python3`, `bash`, `tar`, `gzip`을 사용할 수 있어야 한다.
- 운영 VM에서 Admin Lite 포트 `18080`은 방화벽에 열지 않는다. 관리자 PC에서 SSH 접속에 필요한 포트만 허용한다.

### 2. 설치

OAS VM에서 `oracle` 계정으로 실행한다. 아래 예시는 Git clone 배포다.

```bash
su - oracle
cd /u01
git clone <REPOSITORY_URL> oas-admin-lite
cd /u01/oas-admin-lite
chmod +x scripts/*.sh
./scripts/install.sh /u01/oas-admin-lite
```

`install.sh`는 필요한 데이터·로그 디렉터리를 만들고 `app/config/app.yaml`이 없으면 샘플 파일을 복사한다.

### 3. 단일 VM 설정

`/u01/oas-admin-lite/app/config/app.yaml`의 OAS/OHS 경로는 실제 설치 경로로 수정한다. OHS가 같은 VM에 있고 HTTP 포트가 7777인 경우의 예시는 다음과 같다.

```yaml
server:
  listen: "127.0.0.1:18080"

oas:
  oracle_home: "/u01/app/oracle/product/fmw"
  domain_home: "/u01/app/oracle/config/domains/bi"
  bitools_bin: "/u01/app/oracle/config/domains/bi/bitools/bin"
  catalog_base_url: "http://127.0.0.1:7777"
  catalog_api_path: "/api/20210901/catalog"
  catalog_api_url: ""
  catalog_username: "<OAS_CATALOG_READ_USER>"
  catalog_password: ""

ohs:
  monitor_local: true
  oracle_home: "/u01/app/Oracle/Middleware/ohs_14.1.2"
  domain_home: "/u01/data/domains/ohs_domain"
  instance_name: "ohs1"
  http_port: "7777"
  https_port: ""
```

`catalog_username`과 비밀번호는 Catalog 조회 권한만 가진 전용 계정을 사용한다. 비밀번호는 설정 파일에 저장하지 않고 시작 직전에 환경변수로 전달한다.

```bash
export OAS_ADMIN_LITE_CATALOG_USERNAME="<OAS_CATALOG_READ_USER>"
export OAS_ADMIN_LITE_CATALOG_PASSWORD="<PASSWORD>"
```

### 4. 검증과 시작

```bash
cd /u01/oas-admin-lite
./scripts/healthcheck.sh
./scripts/start.sh
./scripts/status.sh
```

healthcheck가 확인하는 핵심 결과는 다음과 같다.

- Admin Lite의 `127.0.0.1:18080` 사용 가능 여부
- OAS 경로, OPatch, 허용된 관리 스크립트의 존재와 실행 권한
- OHS의 로컬 설치 경로와 `127.0.0.1:7777` listener
- `http://127.0.0.1:7777/api/20210901/catalog`까지의 네트워크 연결

실행 로그는 `/u01/oas-admin-lite/logs/app.log`에서 확인한다. 중지와 재시작은 각각 `./scripts/stop.sh`, `./scripts/start.sh`를 사용한다.

### 5. 관리자 접속

관리자 PC에서 다음 SSH tunnel을 연다.

```bash
ssh -N -L 18080:127.0.0.1:18080 oracle@<OAS_VM_IP>
```

이후 관리자 PC 브라우저에서 다음 주소로 접속한다.

```text
http://127.0.0.1:18080
```

이 구성에서는 Admin Lite와 Catalog REST 구간이 모두 같은 VM 내부의 HTTP 통신이다. SSL과 도메인이 도입되기 전까지는 OHS 또는 방화벽에서 `18080`을 외부에 공개하지 않는다.

이 애플리케이션은 OAS 관리 작업을 실행하는 OAS VM에 배포한다. OAS 실행 파일, `bitools/bin`, OPatch와 로컬 프로세스를 확인해야 하기 때문이다. DB VM과 OHS VM에는 이 파일 경로나 프로세스가 없으므로 동일 인스턴스로 로컬 점검하지 않는다.

```text
관리자 -- SSH tunnel --> Admin Lite (OAS VM) --> OAS 관리 스크립트 / OPatch
                                      |
                                      +--> 내부 LB 또는 OHS Web tier --> OAS REST API

DB VM <-------------------------------- OAS 서비스 연결
```

## 운영 설정

`configs/app.yaml.sample`을 `app/config/app.yaml`로 복사한 뒤 OAS VM의 경로를 입력한다.

```yaml
oas:
  oracle_home: "/u01/app/oracle/product/fmw"
  domain_home: "/u01/app/oracle/config/domains/bi"
  bitools_bin: "/u01/app/oracle/config/domains/bi/bitools/bin"
  catalog_base_url: "https://bi-internal.example.com"
  catalog_api_path: "/api/20210901/catalog"

ohs:
  monitor_local: false
```

`bi-internal.example.com`은 인터넷에 공개하지 않는 내부 LB/Web-tier VIP로 대체한다. OAS VM에서 해당 URL의 TCP 443(또는 지정 포트)에 연결할 수 있도록 방화벽을 허용한다. OHS가 OAS VM에 함께 설치된 단일 VM 환경만 `monitor_local: true`로 설정한다.

## 검증

OAS VM의 `oracle` 계정에서 실행한다.

```bash
./scripts/healthcheck.sh
python3 -m unittest discover -s tests
```

healthcheck에서 다음을 확인한다.

- OAS 경로, OPatch 및 허용된 관리 스크립트의 존재·실행 권한
- Admin Lite 데이터·로그 디렉터리의 쓰기 권한
- Admin Lite listen 주소 사용 가능 여부
- OAS VM에서 내부 Web-tier REST endpoint까지의 TCP 연결성
- `monitor_local: true`일 때만 OHS 경로와 loopback HTTP/HTTPS 포트

Catalog REST API는 Oracle이 Web tier 구성 후 사용하도록 문서화한 API다. 따라서 OAS 관리 포트나 내부 listener를 새로운 REST 공개 경로로 사용하지 않는다. 이 웹앱은 Catalog REST API만으로 Catalog를 수집한다. 실행 시 API가 반환하는 지원 type 목록을 기준으로 Classic 객체(`analysis`, `dashboards`, `dashboardpages`, `reports`)와 DV 객체(`workbooks`, `datasets`, `connections`, `dataflows`, `models`, `sequences`)를 함께 수집한다. `runcat.sh`와 Catalog DB 직접 조회는 수집 경로에 사용하지 않는다.

참고: [OAS REST API 개요](https://docs.oracle.com/en/middleware/bi/analytics-server/oasri/index.html), [Catalog REST endpoint](https://docs.oracle.com/en/middleware/bi/analytics-server/oasri/api-catalog.html)
