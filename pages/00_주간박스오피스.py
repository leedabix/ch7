
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import requests
import streamlit as st


# ----------------------------------------
# 1. 기본 설정
# ----------------------------------------
st.title("🎬 주간 박스오피스 분석")
st.write(
    "KOBIS API로 최근 52주의 주간 박스오피스 정보를 "
    "수집하고, 누적 관객 수 상위 10편을 분석합니다."
)

API_URL = (
    "https://www.kobis.or.kr/kobisopenapi/webservice/rest/"
    "boxoffice/searchWeeklyBoxOfficeList.json"
)

WEEK_COUNT = 52
MAX_REQUESTS = 104
KST = ZoneInfo("Asia/Seoul")

today_kst = datetime.now(KST).date()
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
        "스트림릿 클라우드에서 Settings → Secrets를 열고 "
        'KOBIS_KEY = "발급받은_API_키" 형식으로 등록한 뒤 '
        "앱을 다시 실행하세요."
    )
    st.stop()


# ----------------------------------------
# 3. 기준 날짜 선택
# ----------------------------------------
selected_date = st.date_input(
    "기준 날짜를 선택하세요.",
    value=yesterday,
    max_value=yesterday,
)

st.caption(
    "월요일부터 일요일까지의 주간 박스오피스를 "
    "조회합니다. 선택한 날짜부터 7일씩 이전으로 이동합니다."
)


# ----------------------------------------
# 4. 세션 상태 설정
# ----------------------------------------
STATE_KEY = "kobis_weekly_result"

if STATE_KEY not in st.session_state:
    st.session_state[STATE_KEY] = None


# ----------------------------------------
# 5. API 요청 함수
# ----------------------------------------
def fetch_weekly_boxoffice(target_date, key):
    params = {
        "key": key,
        "targetDt": target_date.strftime("%Y%m%d"),
        "weekGb": "0",
        "itemPerPage": "10",
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            timeout=10,
        )

        if response.status_code != 200:
            if response.status_code in (401, 403):
                return None, "API 인증 또는 사용 권한 오류"

            return None, (
                f"서버 응답 오류(HTTP {response.status_code})"
            )

        try:
            data = response.json()
        except ValueError:
            return None, "JSON 형식으로 응답을 읽을 수 없음"

        if not isinstance(data, dict):
            return None, "응답 형식이 올바르지 않음"

        if "faultInfo" in data or "error" in data:
            return None, (
                "API 요청이 거부되었습니다. "
                "API 키와 요청 변수를 확인하세요."
            )

        result = data.get("boxOfficeResult")

        if not isinstance(result, dict):
            return None, "박스오피스 응답 구조를 찾을 수 없음"

        if not isinstance(result.get("weeklyBoxOfficeList"), list):
            return None, "영화 목록 형식이 올바르지 않음"

        return result, None

    except requests.exceptions.Timeout:
        return None, "10초 이내에 서버 응답이 없음"

    except requests.exceptions.ConnectionError:
        return None, "KOBIS 서버 연결 실패"

    except requests.exceptions.RequestException:
        return None, "네트워크 요청 오류"


# ----------------------------------------
# 6. 집계 기간 변환 함수
# ----------------------------------------
def parse_show_range(show_range):
    # KOBIS 집계 기간 예: 20261102~20261108
    match = re.fullmatch(
        r"\s*(\d{8})\s*~\s*(\d{8})\s*",
        str(show_range),
    )

    if not match:
        return None

    try:
        start = datetime.strptime(
            match.group(1), "%Y%m%d"
        ).date()

        end = datetime.strptime(
            match.group(2), "%Y%m%d"
        ).date()

    except ValueError:
        return None

    # 월요일부터 일요일까지의 기간인지 확인
    if (
        start.weekday() != 0
        or end.weekday() != 6
        or (end - start).days != 6
    ):
        return None

    return start, end


# ----------------------------------------
# 7. 최근 52주 데이터 수집
# ----------------------------------------
if st.button("최근 52주 데이터 불러오기", type="primary"):

    st.session_state[STATE_KEY] = None

    all_movies = []
    collected_periods = set()
    failed_requests = []

    request_count = 0
    success_count = 0
    duplicate_period_count = 0
    empty_period_count = 0

    # 수집 시작 시각
    started_at = datetime.now(KST)

    progress_bar = st.progress(0)
    status_text = st.empty()

    for i in range(MAX_REQUESTS):

        if len(collected_periods) >= WEEK_COUNT:
            break

        target_date = selected_date - timedelta(days=7 * i)
        request_count += 1

        status_text.text(
            f"수집 중: {len(collected_periods)}/{WEEK_COUNT}주 "
            f"(요청 {request_count}회)"
        )

        result, error = fetch_weekly_boxoffice(
            target_date, api_key
        )

        if error:
            failed_requests.append({
                "요청 날짜": target_date,
                "실패 이유": error,
            })
            continue

        show_range = result.get("showRange", "")
        period = parse_show_range(show_range)

        if period is None:
            failed_requests.append({
                "요청 날짜": target_date,
                "실패 이유": "집계 기간 형식 오류",
            })
            continue

        success_count += 1

        start_date, end_date = period
        period_key = (start_date, end_date)

        # 같은 집계 기간은 한 번만 저장
        if period_key in collected_periods:
            duplicate_period_count += 1
            continue

        collected_periods.add(period_key)

        movies = result["weeklyBoxOfficeList"]

        if not movies:
            empty_period_count += 1

        for movie in movies:
            row = movie.copy()

            row["showRange"] = show_range
            row["periodStart"] = start_date
            row["periodEnd"] = end_date
            row["yearWeekTime"] = result.get(
                "yearWeekTime", ""
            )

            all_movies.append(row)

        progress_bar.progress(
            min(len(collected_periods) / WEEK_COUNT, 1.0)
        )

    # 수집 완료 시각
    loaded_at = datetime.now(KST)

    # 현재 사용자 세션에만 결과 저장
    st.session_state[STATE_KEY] = {
        "movies": all_movies,
        "periods": sorted(collected_periods),
        "requests": request_count,
        "successes": success_count,
        "failures": failed_requests,
        "duplicate_periods": duplicate_period_count,
        "empty_periods": empty_period_count,
        "base_date": selected_date,
        "started_at": started_at,
        "loaded_at": loaded_at,
    }

    status_text.empty()

    if len(collected_periods) == WEEK_COUNT:
        st.success("52주 데이터 수집이 완료되었습니다.")
    else:
        st.warning(
            f"52주 중 {len(collected_periods)}주를 수집했습니다. "
            "누락된 주가 있을 수 있습니다."
        )


# ----------------------------------------
# 8. 저장된 수집 결과 확인
# ----------------------------------------
saved = st.session_state[STATE_KEY]

if saved is None:
    st.info(
        "기준 날짜를 선택한 뒤 "
        "'최근 52주 데이터 불러오기' 버튼을 클릭하세요."
    )
    st.stop()

periods = saved["periods"]
failures = saved["failures"]

st.subheader("데이터 수집 결과")

col1, col2, col3, col4 = st.columns(4)

col1.metric("요청 횟수", saved["requests"])
col2.metric("성공 횟수", saved["successes"])
col3.metric("실패 횟수", len(failures))
col4.metric("수집한 주", f"{len(periods)}/{WEEK_COUNT}")

st.write(
    "**데이터 수집 시각:** "
    f"{saved['loaded_at'].strftime('%Y-%m-%d %H:%M:%S')} "
    "(한국 시간)"
)

st.caption(
    f"중복 집계 기간으로 제외한 응답: "
    f"{saved['duplicate_periods']}회"
)

if periods:
    first_start = periods[0][0]
    last_end = periods[-1][1]

    actual_period = (
        f"{first_start.strftime('%Y-%m-%d')} ~ "
        f"{last_end.strftime('%Y-%m-%d')}"
    )

    st.write(f"**실제 수집 기간:** {actual_period}")

    with st.expander("수집한 주별 집계 기간 확인"):
        period_df = pd.DataFrame(
            [
                {
                    "집계 시작일": start,
                    "집계 종료일": end,
                    "영화 수": sum(
                        1
                        for movie in saved["movies"]
                        if (
                            movie.get("periodStart") == start
                            and movie.get("periodEnd") == end
                        )
                    ),
                }
                for start, end in periods
            ]
        )

        st.dataframe(
            period_df,
            hide_index=True,
            use_container_width=True,
        )

if saved["empty_periods"]:
    st.warning(
        f"영화 목록이 비어 있는 집계 기간이 "
        f"{saved['empty_periods']}주 있습니다."
    )

if failures:
    st.warning(
        f"{len(failures)}회 요청에 실패했습니다. "
        "실패한 주의 관객 수를 0으로 대체하지 않습니다."
    )

    with st.expander("요청 실패 내역"):
        st.dataframe(
            pd.DataFrame(failures),
            hide_index=True,
            use_container_width=True,
        )


# ----------------------------------------
# 9. 데이터프레임 생성
# ----------------------------------------
if not saved["movies"]:
    st.info(
        "분석할 영화 데이터가 없습니다. "
        "기준 날짜를 변경해 다시 조회해 보세요."
    )
    st.stop()

df = pd.DataFrame(saved["movies"])

required_columns = [
    "showRange",
    "movieCd",
    "movieNm",
    "openDt",
    "rank",
    "audiCnt",
    "audiAcc",
    "periodStart",
    "periodEnd",
]

for column in required_columns:
    if column not in df.columns:
        df[column] = pd.NA


# ----------------------------------------
# 10. 숫자 및 날짜 변환
# ----------------------------------------
numeric_columns = [
    "rank",
    "audiCnt",
    "audiAcc",
]

numeric_failures = 0

for column in numeric_columns:
    converted = pd.to_numeric(
        df[column], errors="coerce"
    )

    numeric_failures += int(converted.isna().sum())
    df[column] = converted.astype("Int64")

# 개봉일
df["openDt"] = pd.to_datetime(
    df["openDt"],
    format="%Y-%m-%d",
    errors="coerce",
)

# 집계 시작일과 종료일
df["periodStart"] = pd.to_datetime(
    df["periodStart"],
    errors="coerce",
)

df["periodEnd"] = pd.to_datetime(
    df["periodEnd"],
    errors="coerce",
)

date_failures = int(
    df["openDt"].isna().sum()
    + df["periodStart"].isna().sum()
    + df["periodEnd"].isna().sum()
)

total_failures = numeric_failures + date_failures

st.subheader("데이터 변환 확인")

st.write(
    f"숫자 변환 실패: **{numeric_failures}개** / "
    f"날짜 변환 실패: **{date_failures}개**"
)

st.write(
    f"변환하지 못한 값 합계: **{total_failures}개**"
)

st.caption(
    "숫자나 날짜로 변환할 수 없는 값은 결측값으로 "
    "처리하며, 임의로 0으로 변경하지 않습니다."
)


# ----------------------------------------
# 11. 영화 코드 및 중복 확인
# ----------------------------------------
# 영화 코드는 숫자로 변환하지 않고 문자열 유지
df["movieCd"] = (
    df["movieCd"].astype("string").str.strip()
)

df["movieNm"] = df["movieNm"].astype("string")

# 영화 코드가 없는 행은 다른 영화와 구분할 수 없음
invalid_movie_code = (
    df["movieCd"].isna()
    | df["movieCd"].eq("").fillna(False)
)

invalid_code_count = int(invalid_movie_code.sum())

if invalid_code_count:
    st.warning(
        f"영화 코드가 없는 {invalid_code_count}개 행은 "
        "영화별 분석에서 제외합니다."
    )

# 영화 코드와 집계 기간이 같은 중복 행 확인
duplicate_mask = df.duplicated(
    subset=["showRange", "movieCd"],
    keep="first",
)

duplicate_count = int(duplicate_mask.sum())

st.subheader("중복 데이터 확인")
st.write(
    f"영화 코드와 집계 기간이 같은 중복 행: "
    f"**{duplicate_count}개**"
)

if duplicate_count:
    st.warning(
        "중복 데이터가 발견되어 첫 번째 행만 남겼습니다."
    )
    df = df.loc[~duplicate_mask].copy()
else:
    st.success("중복 데이터가 없습니다.")


# ----------------------------------------
# 12. 전체 주간 박스오피스 표
# ----------------------------------------
st.subheader("52주 주간 박스오피스 통합 표")

table_df = df[
    [
        "showRange",
        "movieCd",
        "movieNm",
        "openDt",
        "rank",
        "audiCnt",
        "audiAcc",
    ]
].copy()

table_df.columns = [
    "집계 기간",
    "영화 코드",
    "영화 이름",
    "개봉일",
    "순위",
    "주간 관객 수",
    "누적 관객 수",
]

table_df = table_df.sort_values(
    ["집계 기간", "순위"],
    ascending=[False, True],
    na_position="last",
)

st.write(f"총 **{len(table_df):,}개** 영화 기록")

st.dataframe(
    table_df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "개봉일": st.column_config.DateColumn(
            format="YYYY-MM-DD"
        ),
        "순위": st.column_config.NumberColumn(
            format="%d"
        ),
        "주간 관객 수": st.column_config.NumberColumn(
            format="%d"
        ),
        "누적 관객 수": st.column_config.NumberColumn(
            format="%d"
        ),
    },
)


# ----------------------------------------
# 13. 영화별 가장 최근 기록 찾기
# ----------------------------------------
st.divider()
st.header("🏆 누적 관객 수 TOP 10")

# 영화 코드를 기준으로 동일 영화 구분
analysis_df = df.loc[
    ~invalid_movie_code
].copy()

if analysis_df.empty:
    st.info("영화별 분석에 사용할 데이터가 없습니다.")
    st.stop()

# 영화별 순위표에 등장한 주 수 계산
# 같은 영화가 같은 주에 여러 번 등장해도 1주로 계산
week_counts = (
    analysis_df
    .groupby("movieCd")["showRange"]
    .nunique()
    .reset_index(name="appearedWeeks")
)

# 가장 최근 집계 기간부터 정렬
analysis_df = analysis_df.sort_values(
    ["periodEnd", "periodStart"],
    ascending=[False, False],
    na_position="last",
)

# 영화 코드별 가장 최근 기록 하나만 선택
latest_df = analysis_df.drop_duplicates(
    subset=["movieCd"],
    keep="first",
).copy()

# 등장 주 수 결합
latest_df = latest_df.merge(
    week_counts,
    on="movieCd",
    how="left",
)

# 가장 최근 기록의 누적 관객 수만 사용
# 여러 주의 audiAcc는 절대 합산하지 않음
missing_latest_count = int(
    latest_df["audiAcc"].isna().sum()
)

if missing_latest_count:
    st.warning(
        f"가장 최근 기록에 누적 관객 수가 없는 영화 "
        f"{missing_latest_count}편은 TOP 10 선정에서 제외합니다."
    )

latest_df = latest_df.dropna(
    subset=["audiAcc", "periodEnd"]
).copy()

if latest_df.empty:
    st.info(
        "누적 관객 수를 확인할 수 있는 영화가 없습니다."
    )
    st.stop()


# ----------------------------------------
# 14. 누적 관객 수 상위 10편 선정
# ----------------------------------------
top10_df = (
    latest_df
    .sort_values(
        ["audiAcc", "movieCd"],
        ascending=[False, True],
    )
    .head(10)
    .copy()
)

# 그래프 및 표에서 사용할 집계 기간
top10_df["latestPeriod"] = top10_df.apply(
    lambda row: (
        row["periodStart"].strftime("%Y-%m-%d")
        + " ~ "
        + row["periodEnd"].strftime("%Y-%m-%d")
    ),
    axis=1,
)

top10_df["appearedWeeks"] = (
    top10_df["appearedWeeks"].astype("Int64")
)

# 표에 표시할 순위
top10_df["topRank"] = range(1, len(top10_df) + 1)

# TOP 10 표
top10_table = top10_df[
    [
        "topRank",
        "movieNm",
        "openDt",
        "latestPeriod",
        "audiAcc",
        "appearedWeeks",
    ]
].copy()

top10_table.columns = [
    "순위",
    "영화 이름",
    "개봉일",
    "가장 최근 집계 기간",
    "누적 관객 수",
    "순위표에 등장한 주 수",
]

st.subheader("누적 관객 수 상위 10편")

st.dataframe(
    top10_table,
    hide_index=True,
    use_container_width=True,
    column_config={
        "개봉일": st.column_config.DateColumn(
            format="YYYY-MM-DD"
        ),
        "누적 관객 수": st.column_config.NumberColumn(
            format="%d"
        ),
        "순위표에 등장한 주 수": (
            st.column_config.NumberColumn(format="%d")
        ),
    },
)

st.caption(
    "누적 관객 수는 각 영화가 순위표에 마지막으로 "
    "등장한 주의 기록을 사용했습니다. "
    "여러 주의 누적 관객 수를 더하지 않았습니다."
)


# ----------------------------------------
# 15. TOP 10 가로 막대그래프
# ----------------------------------------
st.subheader("누적 관객 수 TOP 10 그래프")

# 실제로 수집한 기간과 주 수
analyzed_weeks = len(periods)

graph_title = (
    f"누적 관객 수 TOP 10 | "
    f"분석 기간: {actual_period} | "
    f"수집 주 수: {analyzed_weeks}주"
)

chart_df = top10_df.copy()

# 마우스 오버에 표시할 문자열 준비
chart_df["openDtLabel"] = chart_df["openDt"].apply(
    lambda x: (
        x.strftime("%Y-%m-%d")
        if pd.notna(x)
        else "정보 없음"
    )
)

# 값이 큰 영화가 그래프 위쪽에 표시되도록 정렬
chart_df = chart_df.sort_values(
    "audiAcc",
    ascending=True,
)

# 같은 이름의 영화가 있더라도
# 영화 코드별로 별도 막대를 표시
chart_df["movieLabel"] = (
    chart_df["movieNm"].fillna("영화명 없음")
    + " ("
    + chart_df["movieCd"]
    + ")"
)

fig = px.bar(
    chart_df,
    x="audiAcc",
    y="movieLabel",
    orientation="h",
    title=graph_title,
    custom_data=[
        "movieNm",
        "openDtLabel",
        "latestPeriod",
        "audiAcc",
        "appearedWeeks",
    ],
    labels={
        "audiAcc": "누적 관객 수(명)",
        "movieLabel": "영화 이름",
    },
)

# 마우스를 올렸을 때 표시할 정보
fig.update_traces(
    hovertemplate=(
        "영화 이름: %{customdata[0]}<br>"
        "개봉일: %{customdata[1]}<br>"
        "가장 최근 집계 기간: %{customdata[2]}<br>"
        "누적 관객 수: %{customdata[3]:,}명<br>"
        "순위표에 등장한 주 수: %{customdata[4]}주"
        "<extra></extra>"
    )
)

fig.update_layout(
    height=600,
    xaxis_title="누적 관객 수(명)",
    yaxis_title="",
    xaxis_tickformat=",",
    margin=dict(l=20, r=20, t=85, b=20),
)

st.plotly_chart(
    fig,
    use_container_width=True,
)

st.caption(
    f"데이터 수집 시각: "
    f"{saved['loaded_at'].strftime('%Y-%m-%d %H:%M:%S')} "
    "(한국 시간)"
)

st.info(
    "이 분석은 수집된 주간 박스오피스 순위표에 등장한 "
    "영화를 대상으로 합니다. "
    "수집 기간 밖의 영화나 순위표에 등장하지 않은 영화는 "
    "포함되지 않습니다."
)
