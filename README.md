# 센서 데이터 통합 대시보드 (교육용)

스마트폰 phyphox 앱으로 측정한 센서 데이터(가속도, 조도, 기압 등)를 PC에서 그래프와 통계로 보여 주는 Tkinter 프로그램입니다. 비싼 실험 인터페이스 장비 없이 학교 과학실이나 학부 실험실에서 쓸 수 있는 데이터 수집·분석 도구를 목표로 합니다.

## 주제

- 물리 원리: 센서 신호 측정과 변환 (가속도계, 조도 센서, 기압 센서), 단진자 운동, 기압과 고도의 관계
- 알고리즘: 데이터 정규화와 실시간 동기화, 이상치 제거, 이동평균, FFT 주파수 분석
- 개발 방식: 바이브 코딩

## 진행 상황 (1일차)

- [x] phyphox CSV 불러오기 (쉼표·세미콜론·탭 구분, 소수점 쉼표 지원)
- [x] 채널 선택, 채널별 그래프
- [x] 기본 통계 (평균, 표준편차, 최소, 최대)
- [x] 예제 CSV 생성기
- [ ] 이상치 제거 (Hampel 필터), 이동평균
- [ ] FFT로 주 주파수 찾기
- [ ] 기압 → 상대 고도 변환
- [ ] 엑셀(XLSX)·CSV로 내보내기
- [ ] phyphox 실시간 연결 (원격 접속 REST API, 별도 스레드 수집)
- [ ] 시뮬레이션 데모 모드
- [ ] 기록 시작·정지, 최근 N초 자동 스크롤

## 설치

Python 3.9 이상이 필요합니다. Tkinter는 윈도우용 Python에 기본으로 들어 있습니다.

```
pip install -r requirements.txt
```

## 사용법

```
python make_sample_csv.py
python sensor_dashboard.py sample_pendulum.csv
```

'CSV 열기' 버튼으로 다른 파일도 열 수 있습니다. 왼쪽에서 그래프에 표시할 채널을 고르세요.

phyphox에서 측정한 데이터를 쓰려면: 앱에서 측정 → 오른쪽 위 ⋮ → 데이터 내보내기 → CSV (쉼표, 소수점) → PC로 옮겨서 열기.

## 파일 구성

| 파일 | 내용 |
|---|---|
| `sensor_dashboard.py` | GUI (Tkinter + Matplotlib) |
| `sensorlib.py` | 데이터 읽기·계산 모듈 (GUI와 분리) |
| `make_sample_csv.py` | phyphox 형식 예제 CSV 생성기 (진자, 계단 오르기, 조도) |

개발 과정은 [DEVLOG.md](DEVLOG.md)에 정리했습니다.
