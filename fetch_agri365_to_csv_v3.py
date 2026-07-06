# -*- coding: utf-8 -*-
"""
AgriWeather V2 월별-일자료(getWeatherMonDayList) → CSV(date,tavg)  [v3]

v2 대비 변경점 (모두 '왜 실패했는지'를 드러내기 위한 것):
  1) 실패해도 응답 본문/상태코드를 반드시 출력  → 진짜 사유 확인 가능
  2) http → https 로 변경 (data.go.kr 평문 차단 대비)
  3) 인증키를 환경변수(DATA_GO_KR_SERVICE_KEY)에서 우선 로드 (공개 저장소 노출 방지)
  4) data.go.kr 표준 에러(cmmMsgHeader/returnReasonCode) 자동 해석
  5) 특정 월 실패가 전체 실행을 죽이지 않도록 월 단위로 예외 격리(옵션)
"""

import argparse
import os
import time
from datetime import date, datetime
import xml.etree.ElementTree as ET
import requests
import pandas as pd

# ===== 고정 기본값 =====
# ⚠️ 보안: 아래 하드코딩 키는 '공개 저장소에 노출된' 키입니다. 반드시 재발급 후
#    환경변수 DATA_GO_KR_SERVICE_KEY(또는 Streamlit secrets)로 넣어 쓰세요.
SERVICE_KEY_FALLBACK = ""  # 여기 비워두고 환경변수로 주입하는 것을 권장
STATION_DEFAULT      = "441707D001"       # 수원시 서둔동
START_DEFAULT        = "2026-06-28"
OUT_DEFAULT          = "input_daily_avgtemp.csv"
# =======================

# http → https (평문 차단 대비). 문제가 있으면 --endpoint 로 교체 가능
# V3 확정: 기능명이 getWeatherMonDayList → getWeatherMonDayList3 으로 변경됨 (명세서 기준)
ENDPOINT = "https://apis.data.go.kr/1390802/AgriWeather/WeatherObsrInfo/V3/GnrlWeather/getWeatherMonDayList3"
PAGE_SIZE = 100
DEBUG = False

DATE_KEYS = ["date", "obsrDt", "obsDate", "tm"]
TAVG_KEYS = ["temp", "avg_Temp", "avgTa", "taAvg", "avgTemp"]
STN_KEYS  = ["stn_Cd", "obsr_Spot_Code", "stnCd", "stn_cd"]  # 응답의 지점코드 필드 후보

# data.go.kr 공통 에러코드 → 사람이 읽을 수 있는 원인
REASON = {
    "00": "정상(NORMAL)",
    "01": "어플리케이션 에러(APPLICATION_ERROR)",
    "03": "데이터 없음(NODATA_ERROR)",
    "04": "HTTP 에러(HTTP_ERROR)",
    "12": "폐기된 서비스(NO_OPENAPI_SERVICE_ERROR) — 엔드포인트가 사라졌거나 변경됨",
    "20": "서비스 접근 거부(SERVICE_ACCESS_DENIED_ERROR)",
    "22": "요청 제한 초과(트래픽 초과, LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS)",
    "30": "등록되지 않은 서비스키(SERVICE_KEY_IS_NOT_REGISTERED_ERROR)",
    "31": "활용기간 만료(DEADLINE_HAS_EXPIRED_ERROR) — 키를 재발급/연장해야 함",
    "32": "등록되지 않은 IP(UNREGISTERED_IP_ERROR)",
    "99": "기타 에러(UNKNOWN_ERROR)",
}


def resolve_service_key(cli_value: str | None) -> str:
    """우선순위: CLI 인자 > 환경변수 > (마지막 수단) 폴백 상수"""
    key = cli_value or os.environ.get("DATA_GO_KR_SERVICE_KEY") or SERVICE_KEY_FALLBACK
    if not key:
        raise SystemExit(
            "인증키가 없습니다. 다음 중 하나로 주입하세요:\n"
            "  - 환경변수: export DATA_GO_KR_SERVICE_KEY='<Encoding Key 그대로>'\n"
            "  - 실행 인자: --service-key '<Encoding Key>'\n"
            "  - Streamlit Cloud: Settings → Secrets 에 DATA_GO_KR_SERVICE_KEY 등록"
        )
    return key


def month_span(start: date, end: date):
    y, m = start.year, start.month
    while (y < end.year) or (y == end.year and m <= end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _first_text(it: ET.Element, keys: list[str]) -> str | None:
    for k in keys:
        v = it.findtext(k)
        if v and v.strip():
            return v.strip()
    return None


def _to_float(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _interpret_error_body(status_code: int, txt: str) -> str:
    """응답 본문에서 data.go.kr 표준 에러를 찾아 사람이 읽을 메시지로 변환."""
    reason_code = None
    reason_msg = None
    try:
        root = ET.fromstring(txt)
        # cmmMsgHeader(구형) 또는 OpenAPI_ServiceResponse(신형) 어디에 있든 탐색
        reason_code = (root.findtext(".//returnReasonCode")
                       or root.findtext(".//errMsg"))
        reason_msg = (root.findtext(".//returnAuthMsg")
                      or root.findtext(".//errMsg")
                      or root.findtext(".//returnReasonCode"))
        code_only = root.findtext(".//returnReasonCode")
        if code_only and code_only in REASON:
            reason_msg = f"{REASON[code_only]} (code={code_only})"
            reason_code = code_only
    except ET.ParseError:
        pass  # HTML 오류페이지 등 XML이 아닌 경우

    head = f"[HTTP {status_code}]"
    if reason_code:
        return f"{head} data.go.kr 사유: {reason_msg or reason_code}"
    # 표준 에러가 안 잡히면 원문 앞부분을 그대로 노출
    snippet = (txt or "").strip().replace("\n", " ")[:400]
    return f"{head} 표준 에러코드 미검출. 응답 본문 앞부분: {snippet!r}"


def fetch_month(service_key: str, station: str, year: int, month: int,
                endpoint: str = ENDPOINT) -> pd.DataFrame:
    rows = []
    page = 1
    session = requests.Session()
    base = f"{endpoint}?serviceKey={service_key}"  # 키는 여기만(이중 인코딩 방지)

    while True:
        params = {
            "obsr_Spot_Cd": station,        # V3 확정: 지점코드 파라미터명은 obsr_Spot_Cd (구버전 obsr_Spot_Code 아님)
            "search_Year": str(year),
            "search_Month": f"{int(month):02d}",
            "Page_No": page,
            "Page_Size": PAGE_SIZE,
        }
        r = session.get(base, params=params, timeout=30)
        txt = r.text

        if DEBUG:
            with open(f"raw_{year}{month:02d}_p{page}.xml", "w", encoding="utf-8") as f:
                f.write(txt)

        # ── 핵심 수정: 상태코드/본문을 먼저 해석해서 진짜 사유를 던진다 ──
        if r.status_code != 200:
            raise RuntimeError(
                f"{year}-{month:02d} 요청 실패 → " + _interpret_error_body(r.status_code, txt)
            )

        # HTTP 200 이어도 data.go.kr은 본문에 에러를 넣어 보낼 수 있음
        try:
            root = ET.fromstring(txt)
        except ET.ParseError:
            raise RuntimeError(
                f"{year}-{month:02d} 응답이 XML이 아님(HTML 오류페이지 가능). "
                f"앞부분: {txt.strip()[:300]!r}"
            )

        if root.tag == "OpenAPI_ServiceResponse":
            raise RuntimeError(
                f"{year}-{month:02d} 요청 실패 → " + _interpret_error_body(200, txt)
            )
        rc = root.findtext(".//resultCode") or root.findtext(".//result_Code")
        if rc is not None and rc not in ("00", "0", "200"):
            raise RuntimeError(
                f"{year}-{month:02d} resultCode={rc} → " + _interpret_error_body(200, txt)
            )

        items = root.findall(".//items/item")
        if not items:
            break

        for it in items:
            stn = _first_text(it, STN_KEYS)
            if stn is not None and station and stn != station:
                continue  # 요청 지점만 유지: 파라미터가 무시돼도 엉뚱한 지점이 섞이지 않게 방어
            d = _first_text(it, DATE_KEYS)
            t = _first_text(it, TAVG_KEYS)
            tf = _to_float(t)
            if d and tf is not None:
                rows.append({"date": d, "tavg": tf})

        total = int((root.findtext(".//total_Count") or "0"))
        rcdcnt = int((root.findtext(".//rcdcnt") or str(len(items)) or "0"))
        if page * PAGE_SIZE >= total or rcdcnt == 0:
            break
        page += 1
        time.sleep(0.15)

    df = pd.DataFrame(rows, columns=["date", "tavg"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values("date").drop_duplicates("date")
    return df


def main():
    ap = argparse.ArgumentParser(description="AgriWeather V2 일자료 → CSV(date,tavg) [v3]")
    ap.add_argument("--service-key", default=None, help="Encoding Key 그대로(미지정 시 환경변수 사용)")
    ap.add_argument("--station",     default=STATION_DEFAULT)
    ap.add_argument("--start",       default=START_DEFAULT)
    ap.add_argument("--out",         default=OUT_DEFAULT)
    ap.add_argument("--endpoint",    default=ENDPOINT, help="문제 시 다른 엔드포인트로 교체")
    ap.add_argument("--skip-bad-months", action="store_true",
                    help="특정 월 실패해도 나머지는 계속 수집(진단 후 운영용)")
    args = ap.parse_args()

    service_key = resolve_service_key(args.service_key)
    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    today = date.today()

    frames = []
    for y, m in month_span(start_date, today):
        print(f"Fetching {y}-{m:02d} ...")
        try:
            frames.append(fetch_month(service_key, args.station, y, m, args.endpoint))
        except Exception as e:
            print(f"  ⚠️  {y}-{m:02d} 실패: {e}")
            if not args.skip_bad_months:
                raise
        time.sleep(0.15)

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "tavg"])
    if not df.empty:
        df = df[(df["date"] >= start_date) & (df["date"] <= today)].sort_values("date").reset_index(drop=True)
    df.to_csv(args.out, index=False, encoding="utf-8-sig")

    if df.empty:
        print(f"[완료] {args.out} 저장: 데이터 없음")
    else:
        print(f"[완료] {args.out} 저장: {df['date'].min()} ~ {df['date'].max()} ({len(df)}일)")


if __name__ == "__main__":
    main()
