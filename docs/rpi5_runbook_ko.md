# Raspberry Pi 5 RX 노드 운영 런북

본 문서는 Pi 5 + Airspy로 구성된 무인 RX 노드의 일상 점검과 장애 대응
절차를 정리한다. 설치 절차는 `rpi/installation_pi5.md`,
배포 분석/계획은 `docs/rpi5_deployment_report_ko.md` 참조.

---

## 1. 일상 점검 (주 1회)

```bash
# 서비스 상태
systemctl status thriftyx-capture@rx0
journalctl -u thriftyx-capture@rx0 --since "24 hours ago" | tail -50

# 디스크
df -h /var/lib/thriftyx

# 시간 동기
chronyc tracking

# 발열 / 스로틀링
vcgencmd measure_temp
vcgencmd get_throttled       # 0x0 이면 정상

# Airspy 인식
python3 -c "from thriftyx.hal.airspy_mini import list_airspy_serials; print(list_airspy_serials())"
```

---

## 2. 장애 시나리오와 대응

### 2.1 캡처 서비스가 반복 재시작됨
- `journalctl -u thriftyx-capture@rx0 -n 200` 로 마지막 stack/에러 확인
- `airspy_open() failed`: USB 케이블/허브/전원 점검 → 교체 후
  `systemctl restart thriftyx-capture@rx0`
- `DeviceConfigError`: `capture.cfg`의 sample_rate가 장치 지원 범위인지
  확인 (Mini: 3M/6M, R2: 2.5M/10M)

### 2.2 디스크 부족
- cron `cleanup_old_captures.sh` 가 실행되었는지: `journalctl -t thriftyx-cleanup`
- 임시 조치: `find /var/lib/thriftyx/card -type f -mtime +1 -delete`
- 보존정책 변경: `/etc/default/thriftyx-capture@rx0` 에 `CARD_RETENTION_DAYS=N`

### 2.3 스로틀링/발열
- 정상: `get_throttled` = `0x0`
- 비트 16/17/18 set → 과거에 throttle 발생. 쿨러 청소/재장착, 케이스
  통풍 확보, 필요 시 `arm_freq` 약간 하향.

### 2.4 시간 동기 이상
- `chronyc tracking` 의 `Last offset` 가 ±10 ms 이상이면 NTP source
  문제. 노드 간 `chronyc sources -v` 비교.
- WAN 단절 시 일시적 free-run 허용. 복구 후 자동 재동기화.

### 2.5 노드와 서버 간 데이터 전송 실패
- rsync 로그 확인. `~/.ssh/known_hosts` 만료 여부.
- 네트워크 단절은 캡처 자체와 독립 — 캡처는 로컬에 계속 적재됨.

---

## 3. 24시간 soak 테스트 절차

자동화 스크립트(`rpi/soak_test.sh`)를 권장한다 — 24h 캡처 + 1분 단위
헬스 샘플링(CSV) + 자동 PASS/FAIL 판정.

```bash
sudo systemctl stop thriftyx-capture@rx0
~/thrifty-x/rpi/soak_test.sh
# → /var/lib/thriftyx/soak/<timestamp>/{summary.txt,samples.csv,capture.card,...}
echo "exit=$?"   # 0=PASS, 1=FAIL, 2=setup error
```

자동 판정 기준 (환경변수로 재정의 가능):
- 캡처 종료 코드 == 0
- `vcgencmd get_throttled` 가 전 구간 `0x0`
- 피크 CPU 온도 ≤ 80°C (`MAX_TEMP_C`)
- RSS 메모리 증가율 ≤ 10% (초반 vs 종반 중앙값, `MAX_MEM_GROWTH_PCT`)
- 디스크 free ≥ 10% (`MIN_DISK_FREE_PCT`)
- `.card` 파일 헤더 무결성

수동으로 돌리고 싶을 때:

```bash
sudo systemctl stop thriftyx-capture@rx0
source ~/thrifty-x/.venv/bin/activate

OUT=/var/lib/thriftyx/soak/$(date +%Y%m%dT%H%M%S)
mkdir -p "$OUT"

nohup thriftyx capture "$OUT/capture.card" \
    --config /var/lib/thriftyx/capture.cfg \
    --duration 86400 > "$OUT/stdout.log" 2> "$OUT/stderr.log" &

echo $! > "$OUT/pid"
```

24시간 후 합격 판정:
- 프로세스 종료코드 0
- `card` 파일 size 단조 증가, 손상 없음
- `vcgencmd get_throttled` = `0x0`
- 메모리 사용 안정 (peak vs end < 10% 차이)
- 드롭 샘플 카운트(있을 시) 허용치 이하

---

## 4. 헬스체크 / 하트비트

`rpi/heartbeat.py` + systemd timer로 60초 주기 JSON 한 줄을 emit
한다. 기본은 journald 로깅, `THRIFTYX_HEARTBEAT_URL` 설정 시 추가
POST.

설치:

```bash
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-heartbeat.service /etc/systemd/system/
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-heartbeat.timer   /etc/systemd/system/
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-heartbeat.env.example /etc/default/thriftyx-heartbeat
sudo $EDITOR /etc/default/thriftyx-heartbeat   # RXID, OUT, optional URL
sudo systemctl daemon-reload
sudo systemctl enable --now thriftyx-heartbeat.timer
journalctl -t thriftyx-heartbeat -f
```

페이로드 스키마 (HTTP POST JSON, 60초 주기):

```json
{
  "rxid": 0,
  "ts": "2026-05-06T12:34:56Z",
  "uptime_s": 123456,
  "disk_pct": 42,
  "cpu_temp_c": 58.3,
  "throttled": "0x0",
  "service_state": "active",
  "last_detection_ts": "2026-05-06T12:34:50Z",
  "dropped_samples": 0,
  "version": "0.1.0"
}
```

서버에서 60초 내 미수신 시 알림. 수신 엔드포인트는 별도 인프라
(Nginx + 간단한 sink)로 구성한다.

---

## 5. 원격 접속

기존 `rpi/installation.md`의 reverse SSH 섹션이 Pi 5에서도 그대로
유효(autossh + systemd). 다만 weaved 섹션은 deprecated — 무시할 것.

---

## 6. 업데이트 절차

권장: `rpi/update_node.sh` (멱등 wrapper, 자동 롤백).

```bash
sudo install -m 755 ~/thrifty-x/rpi/update_node.sh /usr/local/bin/
ssh rx0 'sudo /usr/local/bin/update_node.sh'
```

동작:
1. `git fetch` 후 변경 없음 → 종료 0 (no-op)
2. `git merge --ff-only` 실패 → 서비스 무손상 종료
3. `pip install` / `restart` / 30초 후 `is-active` 검증
4. 어느 단계든 실패 → 직전 SHA로 자동 롤백 + 재설치 + 재시작
5. 성공 시 새 SHA를 `~/thrifty-x/.last_known_good_sha` 에 기록

종료 코드:
- `0` 최신 또는 업데이트 성공
- `1` 업데이트 실패하나 롤백 성공 (구버전으로 운영 중)
- `2` 업데이트와 롤백 모두 실패 (즉시 사람 개입 필요)
- `3` 셋업 에러 (working tree dirty, venv 없음 등)

수동 절차 (참고):

```bash
ssh rx0
cd ~/thrifty-x
git fetch origin
git log --oneline HEAD..origin/master
git pull --ff-only
source .venv/bin/activate
pip install -e ".[analysis,fft]"
sudo systemctl restart thriftyx-capture@rx0
journalctl -u thriftyx-capture@rx0 -f
```
