# -*- coding: utf-8 -*-
import os
from datetime import timedelta
import pandas as pd
import streamlit as st

CSV_PATH = "input_daily_avgtemp.csv"
THRESHOLD = 1000.0
# ① 기본 시작일 하드코딩 제거 → 데이터 최소 날짜(min_d)를 로드 후 기본값으로 사용

st.set_page_config(page_title="적산온도 1000℃ 모니터", layout="wide")
st.title("수원시 서둔동 적산온도 모니터 (1000℃)")

# ── 표시용 포맷 함수 (④/⑤): None·NaN이면 공란으로 처리 ──────────────
def _fmt1(v):
    """숫자 열: None/NaN → 빈칸, 그 외 소수 1자리"""
    return "" if v is None or pd.isna(v) else f"{v:.1f}"

def _fmtd(v):
    """날짜 열: None/NaN → 빈칸, 그 외 문자열"""
    return "" if v is None or pd.isna(v) else str(v)

# 1) 데이터 로드/검증
if not os.path.exists(CSV_PATH):
    st.error(f"원천 파일이 없습니다: {CSV_PATH}\n먼저 fetch 스크립트를 실행해 주세요.")
    st.stop()

df = pd.read_csv(CSV_PATH)
if df.empty or not set(["date", "tavg"]).issubset(df.columns):
    st.error("CSV에 date,tavg 컬럼이 필요합니다.")
    st.stop()

df["date"] = pd.to_datetime(df["date"]).dt.date
df = df.sort_values("date").reset_index(drop=True)
min_d, max_d = df["date"].min(), df["date"].max()
latest = max_d

# ② 기본 시작일 범위 체크 + st.stop() 블록 제거
#    (①에서 기본값을 항상 데이터 범위 안(min_d)으로 잡으므로 불필요)

# 2) 상단 메트릭
colA, colB, colC = st.columns(3)
colA.metric("스테이션", "수원시 서둔동")
colB.metric("데이터 최신일", f"{latest}")
colC.metric("임계치(℃·일)", f"{int(THRESHOLD)}")

# 3) 사이드바
st.sidebar.header("설정")
# ③ 사이드바 시작일 기본값 → min_d (항상 유효 범위 안에서 시작)
start_date = st.sidebar.date_input(
    "시작일", value=min_d, min_value=min_d, max_value=max_d
)
threshold = st.sidebar.number_input("임계치(℃·일)", value=THRESHOLD, step=50.0)

# 4) 기준일별 표 생성
rows = []
for s in df.loc[df["date"] >= start_date, "date"]:
    day_tavg = float(df.loc[df["date"] == s, "tavg"].iloc[0])
    seg = df[df["date"] >= s].copy()
    seg["cum_deg"] = seg["tavg"].cumsum()

    reached = bool((seg["cum_deg"] >= threshold).any())
    if reached:
        hitrow = seg.loc[seg["cum_deg"] >= threshold].iloc[0]
        chisang = hitrow["date"]                       # 치상일 = 최초 임계 도달일
        hit_val = float(hitrow["cum_deg"])             # 도달적산온도
        tagging = chisang + timedelta(days=7)          # 태깅일 = 치상일 + 7일
    else:
        chisang = None
        hit_val = None
        tagging = None

    total_cum = float(seg["cum_deg"].iloc[-1])          # 적산온도(오늘까지)

    rows.append({
        "날짜": s,
        "평균온도": day_tavg,
        "적산온도": total_cum,
        "도달여부": "TRUE" if reached else "FALSE",
        "치상일": chisang,
        "도달적산온도": hit_val,
        "태깅일(치상+7일)": tagging,
    })

table = pd.DataFrame(rows)

# 5) 요약: 가장 마지막에 도달한 치상일로 표시 (CSV가 바뀌면 자동 갱신)
st.subheader("요약")
reached_df = table[table["도달여부"] == "TRUE"].dropna(subset=["치상일"])
if reached_df.empty:
    st.info(f"아직 어떤 기준일도 임계치({int(threshold)}℃)에 도달하지 않았습니다.")
else:
    last_row = reached_df.loc[reached_df["치상일"].idxmax()]
    st.success(
        f"🟢 가장 마지막 {int(threshold)}℃ 도달: **치상일 {last_row['치상일']}** "
        f"(시작일 {last_row['날짜']}, 도달적산온도 {last_row['도달적산온도']:.1f}, "
        f"태깅일 {last_row['태깅일(치상+7일)']})"
    )

# 6) 표 표시 + 다운로드
st.subheader("기준일별 1000℃ 도달 현황")
st.dataframe(
    # ④/⑤ 포맷 문자열 → None 허용 함수로 교체 (숫자·날짜 열 모두)
    table.style.format({
        "평균온도": _fmt1,
        "적산온도": _fmt1,
        "도달적산온도": _fmt1,
        "치상일": _fmtd,
        "태깅일(치상+7일)": _fmtd,
    }),
    width="stretch",   # ⑥ use_container_width=True → width="stretch"
    height=560,
)

st.download_button(
    "표 CSV 다운로드",
    data=table.to_csv(index=False, encoding="utf-8-sig"),
    file_name="degree_day_summary_by_start.csv",
    mime="text/csv",
)

st.caption("데이터 원천: input_daily_avgtemp.csv (GitHub Actions로 매일 갱신)")
