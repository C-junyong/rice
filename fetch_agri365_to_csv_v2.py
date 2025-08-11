# -*- coding: utf-8 -*-
"""
AgriWeather V2 월별-일자료(getWeatherMonDayList) → CSV(date,tavg)
- Encoding Key(이미 % 포함)는 URL에 직접 붙여 double-encode 방지
- 기본 지점: 수원시 서둔동(441707D001)
- 기본 시작일: 2025-06-28
"""

import argparse
import time
from datetime import date, datetime
import xml.etree.ElementTree as ET
import requests
import pandas as pd

# ===== 고정 기본값 =====
SERVICE_KEY_DEFAULT = "ilBVRGxNjLiAW9NdoYfpzTXAIvz0uzNf2MzMAK%2FzhtKMKQ9xw%2FxJyMmhvE77TMD%2FdqcQYWEVaNuNkig%2F1AzcAw%3D%3D"  # Encoding Key 그대로
STATION_DEFAULT     = "441707D001"       # 수원시 서둔동
START_DEFAULT       = "2025-06-28"
OUT_DEFAULT         = "input_daily_avgtemp.csv"
# =======================

ENDPOINT = "http://apis.data.go.kr/1390802/AgriWeather/WeatherObsrInfo/V2/GnrlWeather/getWeatherMonDayList"
PAGE_SIZE = 100
DEBUG = False  # 필요 시 True로 바꾸면 raw_YYYYMM_pX.xml 저장

DATE_KEYS = ["date", "obsrDt", "obsDate", "tm"]
TAVG_KEYS = ["temp", "avg_Temp", "avgTa", "taAvg", "avgTemp"]

def month_span(start: date, end: date):
    y, m = start.year, start.month
    while (y < end.year) or (y == end.year and m <= end.month):
        yield y, m
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1

def _first_text(it: ET.Element, keys: list[str]) -> str | None:
    for k in keys:
        v = it.findtext(k)
        if v and v.strip():
            return v.strip()
    return None

def _to_float(s: str | None) -> float | None:
    if not s: return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None

def fetch_month(service_key: str, station: str, year: int, month: int) -> pd.DataFrame:
    """Encoding Key는 URL에 직접 붙이고, 나머지 파라미터만 params로 전달"""
    rows = []
    page = 1
    session = requests.Session()

    base = f"{ENDPOINT}?serviceKey={service_key}"  # ← key는 여기만!

    while True:
        params = {
            "obsr_Spot_Code": station,
            "search_Year": str(year),
            "search_Month": f"{int(month):02d}",
            "Page_No": page,
            "Page_Size": PAGE_SIZE,
        }
        r = session.get(base, params=params, timeout=20)
        r.raise_for_status()
        txt = r.text

        if DEBUG:
            with open(f"raw_{year}{month:02d}_p{page}.xml", "w", encoding="utf-8") as f:
                f.write(txt)

        root = ET.fromstring(txt)

        # 에러 포맷(OpenAPI_ServiceResponse) 처리
        if root.tag == "OpenAPI_ServiceResponse":
            err = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg")
            code = root.findtext(".//returnReasonCode")
            raise RuntimeError(f"API error: {err} (code={code})")

        items = root.findall(".//items/item")
        if DEBUG:
            total  = int((root.findtext(".//total_Count") or "0"))
            rcdcnt = int((root.findtext(".//rcdcnt") or str(len(items)) or "0"))
            print(f"[DEBUG] {year}-{month:02d} page {page}  items={len(items)}  rcdcnt={rcdcnt}  total={total}")

        if not items:
            break

        for it in items:
            d = _first_text(it, DATE_KEYS)
            t = _first_text(it, TAVG_KEYS)
            tf = _to_float(t)
            if d and tf is not None:
                rows.append({"date": d, "tavg": tf})

        total  = int((root.findtext(".//total_Count") or "0"))
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
    ap = argparse.ArgumentParser(description="AgriWeather V2 일자료 → CSV(date,tavg)")
    ap.add_argument("--service-key", default=SERVICE_KEY_DEFAULT, help="Encoding Key 그대로(%2F,%3D 포함)")
    ap.add_argument("--station",     default=STATION_DEFAULT,     help="관측지점코드")
    ap.add_argument("--start",       default=START_DEFAULT,       help="시작일(YYYY-MM-DD)")
    ap.add_argument("--out",         default=OUT_DEFAULT,         help="출력 CSV 경로")
    args = ap.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    today = date.today()

    frames = []
    for y, m in month_span(start_date, today):
        print(f"Fetching {y}-{m:02d} ...")
        frames.append(fetch_month(args.service_key, args.station, y, m))
        time.sleep(0.15)

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date","tavg"])
    if not df.empty:
        df = df[(df["date"] >= start_date) & (df["date"] <= today)].sort_values("date").reset_index(drop=True)
    df.to_csv(args.out, index=False, encoding="utf-8-sig")

    if df.empty:
        print(f"[완료] {args.out} 저장: 데이터 없음")
    else:
        print(f"[완료] {args.out} 저장: {df['date'].min()} ~ {df['date'].max()} ({len(df)}일)")

if __name__ == "__main__":
    main()