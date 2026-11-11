
import streamlit as st
import pandas as pd
import plotly.express as px
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ----------------------------------------
# 1. 기본 설정
# ----------------------------------------
st.set_page_config(
    page_title="일별 박스오피스 대시보드",
    page_icon="🎬",
    layout="wide",
)

st.title("🎬 일별 박스오피스 대시보드")
st.write("영화진흥위원회 KOBIS API로 일별 박스오피스 정보를 조회합니다.")

API_URL = (
    "https://www.kobis.or.kr/kobisopenapi/webservice/rest/"
    "boxoffice/searchDailyBoxOfficeList.json"
)

# 한국 시간 기준 어제
today_kst = datetime.now(ZoneInfo("Asia/Seoul")).date()
yesterday = today_kst - timedelta(days=1)


# ----------------------------------------
# 2. API 키 확인
# ----------------------------------------
try:
    api_key = st.secrets.get("KOBIS_KEY")
except Exception:
    api_key = None

if not api_key or not str(api_key).strip():
    st.error("KOBIS API 키가 등록되지 않았습니다.")
    st.info(
        "스트림릿 클라우드에서 앱의 Settings → Secrets를 열고 "
        'KOBIS_KEY = "발급받은_API_키" 형식으로 등록해 주세요. '
        "저장한 뒤 앱을 다시 실행하면 됩니다."
    )
    st.stop()


# ----------------------------------------
# 3. 조회 날짜 선택
# ----------------------------------------
selected_date = st.date_input(
    "조회 날짜를 선택하세요.",
    value=yesterday,
    max_value=yesterday,
)

target_dt = selected_date.strftime("%Y%m%d")


# ----------------------------------------
# 4. API 데이터 요청
# ----------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_boxoffice(target_dt, _api_key):
    params = {
        "key": _api_key,
        "targetDt": target_dt,
        "itemPerPage": "10",
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            timeout=10,
        )
    except requests.exceptions.Timeout:
        return None, (
            "요청 후 10초 동안 응답이 없었습니다. "
            "인터넷 연결이나 KOBIS 서버 상태를 확인해 주세요."
        )
    except requests.exceptions.ConnectionError:
        return None, (
            "KOBIS 서버에 연결하지 못했습니다. "
            "인터넷 연결이나 서버 상태를 확인해 주세요."
        )
    except requests.exceptions.RequestException:
        return None, (
            "API 요청 중 문제가 발생했습니다. "
            "잠시 후 다시 시도해 주세요."
        )

    if response.status_code in (401, 403):
        return None, (
            "인증이 거부되었습니다. "
            "KOBIS API 키가 올바른지 확인해 주세요."
        )

    if response.status_code != 200:
        return None, (
            f"KOBIS 서버에서 오류가 발생했습니다. "
            f"(HTTP 상태 코드: {response.status_code}) "
            "요청 날짜나 서버 상태를 확인해 주세요."
        )

    try:
        data = response.json()
    except ValueError:
        return None, (
            "서버 응답을 JSON 형식으로 읽지 못했습니다. "
            "KOBIS 서버 상태를 확인해 주세요."
        )

    if not isinstance(data, dict):
        return None, "서버 응답 형식이 예상과 다릅니다."

    if "faultInfo" in data:
        return None, (
            "KOBIS API에서 요청을 처리하지 못했습니다. "
            "API 키, 조회 날짜, API 사용 권한을 확인해 주세요."
        )

    result = data.get("boxOfficeResult")

    if not isinstance(result, dict):
        return None, (
            "박스오피스 정보를 찾지 못했습니다. "
            "API 키나 조회 날짜를 확인해 주세요."
        )

    return result, None


with st.spinner("박스오피스 데이터를 불러오는 중입니다..."):
    result, error = load_boxoffice(target_dt, api_key)

if error:
    st.error(error)
    st.stop()


# ----------------------------------------
# 5. 조회 결과 확인
# ----------------------------------------
movies = result.get("dailyBoxOfficeList", [])
show_range = result.get("showRange", target_dt)

st.subheader("조회 결과")

col1, col2 = st.columns(2)
col1.metric("실제 조회 기간", show_range)
col2.metric("불러온 영화 수", len(movies))

if not movies:
    st.info(
        "선택한 날짜에 조회된 영화가 없습니다. "
        "다른 날짜를 선택해 보세요."
    )
    st.stop()


# ----------------------------------------
# 6. 데이터프레임 생성 및 숫자 변환
# ----------------------------------------
df = pd.DataFrame(movies)

numeric_columns = [
    "rank",
    "audiCnt",
    "audiAcc",
    "scrnCnt",
    "showCnt",
]

failed_count = 0

for column in numeric_columns:
    if column not in df.columns:
        df[column] = pd.NA

    original = df[column]
    converted = pd.to_numeric(original, errors="coerce")

    # 숫자로 변환하지 못한 값(빈 값 포함) 계산
    failed_count += int(converted.isna().sum())

    df[column] = converted.astype("Int64")

st.caption(
    f"숫자 변환 확인: {len(df) * len(numeric_columns)}개 값 중 "
    f"{failed_count}개를 숫자로 변환하지 못했습니다. "
    "변환하지 못한 값은 빈칸으로 표시합니다."
)


# ----------------------------------------
# 7. 표 출력
# ----------------------------------------
st.subheader("일별 박스오피스 순위")

table_columns = {
    "rank": "순위",
    "movieNm": "영화 이름",
    "openDt": "개봉일",
    "audiCnt": "당일 관객 수",
    "audiAcc": "누적 관객 수",
    "scrnCnt": "스크린 수",
    "showCnt": "상영 횟수",
}

table_df = df[list(table_columns.keys())].copy()
table_df = table_df.rename(columns=table_columns)
table_df = table_df.sort_values("순위", na_position="last")

st.dataframe(
    table_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "당일 관객 수": st.column_config.NumberColumn(format="%d"),
        "누적 관객 수": st.column_config.NumberColumn(format="%d"),
        "스크린 수": st.column_config.NumberColumn(format="%d"),
        "상영 횟수": st.column_config.NumberColumn(format="%d"),
    },
)


# ----------------------------------------
# 8. 가로 막대그래프
# ----------------------------------------
st.subheader("영화별 당일 관객 수")

chart_df = df.dropna(subset=["audiCnt"]).copy()
chart_df = chart_df.sort_values("audiCnt", ascending=True)

if chart_df.empty:
    st.info(
        "그래프에 사용할 당일 관객 수 데이터가 없습니다. "
        "숫자 변환 결과를 확인해 주세요."
    )
else:
    # 마우스 오버에 사용할 개봉일과 누적 관객 수
    chart_df["openDt"] = chart_df["openDt"].fillna("정보 없음")
    chart_df["audiAccLabel"] = chart_df["audiAcc"].apply(
        lambda x: f"{int(x):,}명" if pd.notna(x) else "정보 없음"
    )

    fig = px.bar(
        chart_df,
        x="audiCnt",
        y="movieNm",
        orientation="h",
        custom_data=[
            "movieNm",
            "openDt",
            "audiCnt",
            "audiAccLabel",
        ],
        labels={
            "audiCnt": "당일 관객 수(명)",
            "movieNm": "영화 이름",
        },
    )

    fig.update_traces(
        hovertemplate=(
            "영화 이름: %{customdata[0]}<br>"
            "개봉일: %{customdata[1]}<br>"
            "당일 관객 수: %{customdata[2]:,}명<br>"
            "누적 관객 수: %{customdata[3]}"
            "<extra></extra>"
        )
    )

    fig.update_layout(
        xaxis_title="당일 관객 수(명)",
        yaxis_title="",
        height=max(450, len(chart_df) * 45),
        margin=dict(l=20, r=20, t=20, b=20),
    )

    st.plotly_chart(fig, use_container_width=True)
