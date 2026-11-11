
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st


# ----------------------------------------
# 1. 기본 설정
# ----------------------------------------
st.title("🎬 역주행 영화 찾기")
st.write(
    "KOBIS 주간 박스오피스 데이터를 분석해 "
    "개봉 후 관객 수가 뒤늦게 증가한 영화를 찾습니다."
)

API_URL = (
    "https://www.kobis.or.kr/kobisopenapi/webservice/rest/"
    "boxoffice/searchWeeklyBoxOfficeList.json"
)

KST = ZoneInfo("Asia/Seoul")
CACHE_SECONDS = 600
REQUEST_TIMEOUT = 10
REQUEST_INTERVAL = 0.2
MAX_RETRIES = 2

# 예상 시간 계산에 사용할 가정
ASSUMED_SECONDS = 1.5

# 한국 시간 기준 가장 최근에 완료된 주의 일요일
today = datetime.now(KST).date()
last_sunday = today - timedelta(days=today.weekday() + 1)


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
        "스트림릿 클라우드의 Settings → Secrets에서 "
        'KOBIS_KEY = "발급받은_API_키"를 등록하고 '
        "앱을 다시 실행하세요."
    )
    st.stop()


# ----------------------------------------
# 3. 분석 기간 및 예상 소요 시간
# ----------------------------------------
years = st.selectbox(
    "분석할 기간을 선택하세요.",
    options=list(range(1, 11)),
    index=4,
    format_func=lambda x: f"{x}년",
)

week_count = years * 52

# 최초 요청만 성공하고 재시도가 없을 때의 예상 시간
estimated_seconds = (
    week_count * ASSUMED_SECONDS
    + max(0, week_count - 1) * REQUEST_INTERVAL
)

estimated_minutes = estimated_seconds / 60

st.write(f"**예정된 주간 데이터 요청: {week_count:,}주**")
st.write(f"**기본 요청 횟수: {week_count:,}회**")
st.write(
    f"**예상 소요 시간: 약 {estimated_minutes:.1f}분**"
)

st.caption(
    f"API 요청 한 번에 평균 {ASSUMED_SECONDS}초, "
    f"요청 간격 {REQUEST_INTERVAL}초를 가정했습니다. "
    "실제 소요 시간은 서버 응답 속도와 재시도에 따라 "
    "더 길어질 수 있습니다."
)

st.warning(
    "장기간 조회 시 API 호출 한도나 "
    "스트림릿 클라우드의 실행 제한 때문에 "
    "수집이 중단될 수 있습니다. "
    "처음에는 1년으로 테스트하는 것을 권장합니다."
)


# ----------------------------------------
# 4. 세션 캐시 설정
# ----------------------------------------
CACHE_KEY = "kobis_reverse_movie_cache"

if CACHE_KEY not in st.session_state:
    st.session_state[CACHE_KEY] = None


def get_cached_result():
    saved = st.session_state[CACHE_KEY]

    if saved is None:
        return None

    age = (
        datetime.now(KST) - saved["loaded_at"]
    ).total_seconds()

    if age >= CACHE_SECONDS:
        st.session_state[CACHE_KEY] = None
        return None

    # 분석 기간이나 기준 주가 바뀌면 재사용하지 않음
    if (
        saved["years"] != years
        or saved["last_sunday"] != last_sunday
    ):
        return None

    return saved


# ----------------------------------------
# 5. 집계 기간 변환
# ----------------------------------------
def parse_show_range(value):
    match = re.fullmatch(
        r"\s*(\d{8})\s*~\s*(\d{8})\s*",
        str(value),
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

    if (
        start.weekday() != 0
        or end.weekday() != 6
        or (end - start).days != 6
    ):
        return None

    return start, end


# ----------------------------------------
# 6. API 호출 함수
# ----------------------------------------
def fetch_week(target_date, key, counters):
    params = {
        "key": key,
        "targetDt": target_date.strftime("%Y%m%d"),
        "weekGb": "0",
        "itemPerPage": "10",
    }

    last_error = "알 수 없는 오류"

    # 최초 요청 1회 + 재시도 최대 2회
    for attempt in range(MAX_RETRIES + 1):
        if counters["requests"] > 0:
            time.sleep(REQUEST_INTERVAL)

        counters["requests"] += 1

        try:
            response = requests.get(
                API_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code in (401, 403):
                return None, "API 인증 또는 권한 오류", True

            if response.status_code == 429:
                last_error = "API 요청 한도 초과"
            elif response.status_code >= 500:
                last_error = (
                    f"서버 오류(HTTP {response.status_code})"
                )
            elif response.status_code != 200:
                return (
                    None,
                    f"요청 오류(HTTP {response.status_code})",
                    False,
                )
            else:
                try:
                    data = response.json()
                except ValueError:
                    last_error = "JSON 응답 해석 실패"
                    continue

                if not isinstance(data, dict):
                    last_error = "응답 형식 오류"
                    continue

                if "faultInfo" in data or "error" in data:
                    return (
                        None,
                        "API 키 또는 요청 변수 확인 필요",
                        True,
                    )

                result = data.get("boxOfficeResult")

                if not isinstance(result, dict):
                    last_error = "박스오피스 응답 누락"
                    continue

                if not isinstance(
                    result.get("weeklyBoxOfficeList"), list
                ):
                    last_error = "영화 목록 형식 오류"
                    continue

                return result, None, False

        except requests.exceptions.Timeout:
            last_error = "10초 동안 응답 없음"

        except requests.exceptions.ConnectionError:
            last_error = "서버 연결 실패"

        except requests.exceptions.RequestException:
            last_error = "네트워크 요청 오류"

        # API 호출 한도 초과 시 재시도 전 추가 대기
        if attempt < MAX_RETRIES and last_error == "API 요청 한도 초과":
            time.sleep(2 * (attempt + 1))

    return None, last_error, False


# ----------------------------------------
# 7. 데이터 수집
# ----------------------------------------
if st.button("박스오피스 데이터 불러오기", type="primary"):

    cached = get_cached_result()

    if cached is not None:
        st.info(
            "10분 이내에 불러온 데이터를 재사용합니다. "
            "새로운 API 요청을 보내지 않았습니다."
        )

    else:
        st.session_state[CACHE_KEY] = None

        records = []
        periods = set()
        failures = []

        counters = {"requests": 0}
        success_count = 0
        duplicate_count = 0

        progress = st.progress(0)
        status = st.empty()

        started_at = datetime.now(KST)

        for i in range(week_count):
            target_date = last_sunday - timedelta(
                days=i * 7
            )

            result, error, fatal = fetch_week(
                target_date, api_key, counters
            )

            if error:
                failures.append({
                    "요청 날짜": target_date,
                    "실패 이유": error,
                })
            else:
                period = parse_show_range(
                    result.get("showRange", "")
                )

                # 요청한 월~일 집계 기간과 일치하는지 확인
                expected_start = target_date - timedelta(days=6)
                expected_period = (
                    expected_start, target_date
                )

                if period != expected_period:
                    failures.append({
                        "요청 날짜": target_date,
                        "실패 이유": "집계 기간 불일치 또는 형식 오류",
                    })

                elif period in periods:
                    duplicate_count += 1

                else:
                    periods.add(period)
                    success_count += 1

                    for movie in result["weeklyBoxOfficeList"]:
                        row = movie.copy()

                        row["showRange"] = result["showRange"]
                        row["periodStart"] = period[0]
                        row["periodEnd"] = period[1]

                        records.append(row)

            progress.progress((i + 1) / week_count)

            status.text(
                f"진행: {i + 1}/{week_count}주 | "
                f"실제 요청 {counters['requests']}회 | "
                f"성공 {success_count}주 | "
                f"실패 {len(failures)}주"
            )

            if fatal:
                st.error(
                    "인증 또는 API 사용 권한 문제가 발생해 "
                    "추가 요청을 중단했습니다."
                )
                break

        loaded_at = datetime.now(KST)

        st.session_state[CACHE_KEY] = {
            "years": years,
            "last_sunday": last_sunday,
            "records": records,
            "periods": sorted(periods),
            "failures": failures,
            "requests": counters["requests"],
            "successes": success_count,
            "duplicates": duplicate_count,
            "loaded_at": loaded_at,
            "started_at": started_at,
        }

        status.empty()
        st.success("데이터 수집 작업이 종료되었습니다.")


# ----------------------------------------
# 8. 수집 결과 표시
# ----------------------------------------
saved = get_cached_result()

if saved is None:
    st.info(
        "분석 기간을 선택하고 데이터 불러오기 버튼을 클릭하세요."
    )
    st.stop()

periods = saved["periods"]
records = saved["records"]

st.subheader("데이터 수집 결과")

a, b, c = st.columns(3)

a.metric("실제 API 요청 횟수", saved["requests"])
b.metric("성공한 주", saved["successes"])
c.metric("실패한 주", len(saved["failures"]))

st.caption(
    "요청 횟수에는 재시도가 포함됩니다. "
    "성공·실패 횟수는 서로 다른 주를 기준으로 계산합니다."
)

st.write(
    "**데이터 수집 시각:** "
    f"{saved['loaded_at'].strftime('%Y-%m-%d %H:%M:%S')} "
    "(한국 시간)"
)

remaining = CACHE_SECONDS - (
    datetime.now(KST) - saved["loaded_at"]
).total_seconds()

st.caption(
    f"현재 세션의 데이터 유지 시간: "
    f"약 {max(0, remaining) / 60:.1f}분 남음"
)

if periods:
    actual_period = (
        f"{periods[0][0]} ~ {periods[-1][1]}"
    )
    st.write(f"**실제로 불러온 기간:** {actual_period}")
else:
    st.warning("성공적으로 수집한 집계 기간이 없습니다.")
    st.stop()

st.write(f"**서로 다른 집계 주 수:** {len(periods)}주")

if saved["duplicates"]:
    st.warning(
        f"중복된 집계 기간 {saved['duplicates']}건을 "
        "수집 결과에서 제외했습니다."
    )

if saved["failures"]:
    st.warning(
        "실패한 주의 관객 수는 0으로 대체하지 않았습니다."
    )

    with st.expander("요청 실패 내역"):
        st.dataframe(
            pd.DataFrame(saved["failures"]),
            hide_index=True,
            use_container_width=True,
        )

if not records:
    st.info("분석할 영화 기록이 없습니다.")
    st.stop()


# ----------------------------------------
# 9. 데이터프레임과 자료형 변환
# ----------------------------------------
df = pd.DataFrame(records)

columns = [
    "showRange",
    "periodStart",
    "periodEnd",
    "rank",
    "movieCd",
    "movieNm",
    "openDt",
    "audiCnt",
    "audiAcc",
]

for column in columns:
    if column not in df.columns:
        df[column] = pd.NA

# 영화 코드는 문자열로 유지
df["movieCd"] = (
    df["movieCd"].astype("string").str.strip()
)
df["movieNm"] = df["movieNm"].astype("string")

numeric_failures = 0

for column in ["rank", "audiCnt", "audiAcc"]:
    converted = pd.to_numeric(
        df[column], errors="coerce"
    )
    numeric_failures += int(converted.isna().sum())
    df[column] = converted.astype("Int64")

date_failures = 0

for column in ["openDt", "periodStart", "periodEnd"]:
    if column == "openDt":
        converted = pd.to_datetime(
            df[column],
            format="%Y-%m-%d",
            errors="coerce",
        )
    else:
        converted = pd.to_datetime(
            df[column], errors="coerce"
        )

    date_failures += int(converted.isna().sum())
    df[column] = converted

# 동일 영화·동일 집계 주 중복 제거
duplicate_mask = df.duplicated(
    ["movieCd", "periodStart"],
    keep="first",
)

row_duplicates = int(duplicate_mask.sum())
df = df.loc[~duplicate_mask].copy()

valid_codes = (
    df["movieCd"].notna()
    & df["movieCd"].ne("").fillna(False)
)

unique_movies = df.loc[
    valid_codes, "movieCd"
].nunique()

st.subheader("데이터 현황")

a, b, c = st.columns(3)

a.metric("서로 다른 영화 수", unique_movies)
b.metric("전체 행 수", len(df))
c.metric("중복 제거 행 수", row_duplicates)

st.write(
    f"숫자 변환 실패: **{numeric_failures:,}개** / "
    f"날짜 변환 실패: **{date_failures:,}개**"
)

st.write(
    f"변환 실패 합계: "
    f"**{numeric_failures + date_failures:,}개**"
)

st.caption(
    "빈 값과 잘못된 형식은 결측값으로 유지하며, "
    "임의로 0을 입력하지 않습니다."
)


# ----------------------------------------
# 10. 전체 주간 박스오피스 표
# ----------------------------------------
st.subheader("수집한 주간 박스오피스")

table = df[
    [
        "showRange",
        "rank",
        "movieCd",
        "movieNm",
        "openDt",
        "audiCnt",
        "audiAcc",
    ]
].copy()

table.columns = [
    "집계 기간",
    "순위",
    "영화 코드",
    "영화 이름",
    "개봉일",
    "주간 관객 수",
    "누적 관객 수",
]

table = table.sort_values(
    ["집계 기간", "순위"],
    ascending=[False, True],
    na_position="last",
)

st.dataframe(
    table,
    hide_index=True,
    use_container_width=True,
    column_config={
        "개봉일": st.column_config.DateColumn(
            format="YYYY-MM-DD"
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
# 11. 개봉 후 주차 계산
# ----------------------------------------
analysis = df.loc[
    valid_codes
    & df["openDt"].notna()
    & df["periodStart"].notna()
].copy()

if analysis.empty:
    st.info("개봉일이 확인되는 영화가 없습니다.")
    st.stop()

# 영화 개봉일이 포함된 월요일을 1주차로 설정
analysis["releaseMonday"] = (
    analysis["openDt"]
    - pd.to_timedelta(
        analysis["openDt"].dt.weekday,
        unit="D",
    )
)

analysis["weekNum"] = (
    (
        analysis["periodStart"]
        - analysis["releaseMonday"]
    ).dt.days // 7
) + 1

# 개봉 전 기록은 분석에서 제외
analysis = analysis.loc[
    analysis["weekNum"] >= 1
].copy()

# 영화 코드별로 개봉 후 첫 8주 분석
first8 = analysis.loc[
    analysis["weekNum"].between(1, 8)
].copy()


# ----------------------------------------
# 12. 역주행 후보 찾기
# ----------------------------------------
candidates = []

excluded_early = 0
excluded_incomplete = 0

for movie_cd, group in first8.groupby("movieCd"):

    group = group.sort_values("weekNum")
    group = group.drop_duplicates(
        subset=["weekNum"],
        keep="first",
    )

    weeks = set(group["weekNum"].tolist())

    # 첫 8주에 기록이 4개 이상 있어야 함
    # 1주차와 2주차 기록도 모두 필요
    if (
        len(group) < 4
        or 1 not in weeks
        or 2 not in weeks
    ):
        excluded_incomplete += 1
        continue

    early = group.loc[
        group["weekNum"].isin([1, 2]),
        "audiCnt",
    ]

    # 1·2주차에 유효한 관객 수가 모두 있어야 함
    if early.isna().any() or (early <= 0).all():
        excluded_early += 1
        continue

    early_max = int(early.max())

    if early_max <= 0:
        excluded_early += 1
        continue

    later = group.loc[
        group["weekNum"].between(3, 8)
        & group["audiCnt"].notna()
    ].copy()

    if later.empty:
        excluded_incomplete += 1
        continue

    # 가장 높은 관객 수를 기록한 주
    later = later.sort_values(
        ["audiCnt", "weekNum"],
        ascending=[False, True],
    )

    peak = later.iloc[0]
    peak_audience = int(peak["audiCnt"])

    ratio = peak_audience / early_max

    # 초반보다 이후 관객 수가 많을 때만 후보
    if peak_audience <= early_max:
        continue

    movie_info = group.sort_values(
        "periodStart"
    ).iloc[-1]

    candidates.append({
        "movieCd": movie_cd,
        "movieNm": movie_info["movieNm"],
        "openDt": movie_info["openDt"],
        "earlyAudience": early_max,
        "peakWeek": int(peak["weekNum"]),
        "peakAudience": peak_audience,
        "ratio": ratio,
    })

candidate_df = pd.DataFrame(candidates)

st.divider()
st.header("🏆 역주행 영화 후보 TOP 10")

st.write(
    f"1·2주 차의 관객 수가 0이거나 유효하지 않아 "
    f"제외한 영화: **{excluded_early:,}편**"
)

st.write(
    f"필요한 주차 기록이 부족하여 제외한 영화: "
    f"**{excluded_incomplete:,}편**"
)

if candidate_df.empty:
    st.info(
        "선택한 분석 기간에서 조건을 만족하는 "
        "역주행 후보를 찾지 못했습니다."
    )
    st.stop()

candidate_df = candidate_df.sort_values(
    ["ratio", "movieCd"],
    ascending=[False, True],
)

top10 = candidate_df.head(10).copy()

top10["topRank"] = range(1, len(top10) + 1)

top_table = top10[
    [
        "topRank",
        "movieNm",
        "openDt",
        "earlyAudience",
        "peakWeek",
        "peakAudience",
        "ratio",
    ]
].copy()

top_table.columns = [
    "순위",
    "영화 이름",
    "개봉일",
    "초반 관객 수",
    "정점 주차",
    "정점 관객 수",
    "증가 배율",
]

st.dataframe(
    top_table,
    hide_index=True,
    use_container_width=True,
    column_config={
        "개봉일": st.column_config.DateColumn(
            format="YYYY-MM-DD"
        ),
        "초반 관객 수": st.column_config.NumberColumn(
            format="%d"
        ),
        "정점 관객 수": st.column_config.NumberColumn(
            format="%d"
        ),
        "증가 배율": st.column_config.NumberColumn(
            format="%.2f배"
        ),
    },
)


# ----------------------------------------
# 13. 증가 배율 가로 막대그래프
# ----------------------------------------
st.subheader("역주행 후보별 증가 배율")

chart_df = top10.sort_values(
    "ratio", ascending=True
).copy()

chart_df["movieLabel"] = (
    chart_df["movieNm"].fillna("영화명 없음")
    + " ("
    + chart_df["movieCd"]
    + ")"
)

chart_df["openDtLabel"] = (
    chart_df["openDt"].dt.strftime("%Y-%m-%d")
)

fig = px.bar(
    chart_df,
    x="ratio",
    y="movieLabel",
    orientation="h",
    custom_data=[
        "movieNm",
        "openDtLabel",
        "earlyAudience",
        "peakWeek",
        "peakAudience",
        "ratio",
    ],
    labels={
        "ratio": "증가 배율(배)",
        "movieLabel": "영화 이름",
    },
)

fig.update_traces(
    hovertemplate=(
        "영화 이름: %{customdata[0]}<br>"
        "개봉일: %{customdata[1]}<br>"
        "초반 관객 수: %{customdata[2]:,}명<br>"
        "정점 주차: %{customdata[3]}주차<br>"
        "정점 관객 수: %{customdata[4]:,}명<br>"
        "증가 배율: %{customdata[5]:.2f}배"
        "<extra></extra>"
    )
)

fig.update_layout(
    height=550,
    xaxis_title="증가 배율(배)",
    yaxis_title="",
    margin=dict(l=20, r=20, t=30, b=20),
)

st.plotly_chart(
    fig,
    use_container_width=True,
)


# ----------------------------------------
# 14. 후보 영화 선택
# ----------------------------------------
st.divider()
st.header("📈 영화별 관객 수 추이")

movie_options = top10["movieCd"].tolist()

movie_names = dict(
    zip(
        top10["movieCd"],
        top10["movieNm"],
    )
)

selected_movie = st.selectbox(
    "관객 수 추이를 확인할 영화를 선택하세요.",
    options=movie_options,
    format_func=lambda code: (
        f"{movie_names[code]} ({code})"
    ),
)

selected_info = top10.loc[
    top10["movieCd"] == selected_movie
].iloc[0]

selected_data = analysis.loc[
    (analysis["movieCd"] == selected_movie)
    & analysis["weekNum"].between(1, 20)
].copy()

selected_data = selected_data.sort_values(
    "periodStart"
).drop_duplicates("weekNum")

# 1주차부터 20주차까지 모든 주차 생성
weeks20 = pd.DataFrame({
    "weekNum": list(range(1, 21))
})

# 기록이 없는 주차는 결측값으로 유지
trend = weeks20.merge(
    selected_data[
        [
            "weekNum",
            "rank",
            "audiCnt",
        ]
    ],
    on="weekNum",
    how="left",
)

release_monday = (
    selected_info["openDt"]
    - pd.Timedelta(
        days=selected_info["openDt"].weekday()
    )
)

trend["weekStart"] = [
    release_monday + pd.Timedelta(weeks=i - 1)
    for i in trend["weekNum"]
]

trend["weekStartLabel"] = (
    trend["weekStart"].dt.strftime("%Y-%m-%d")
)

trend["rankLabel"] = trend["rank"].apply(
    lambda x: (
        str(int(x)) if pd.notna(x)
        else "기록 없음"
    )
)

trend["audienceLabel"] = trend["audiCnt"].apply(
    lambda x: (
        f"{int(x):,}명"
        if pd.notna(x)
        else "기록 없음"
    )
)

trend["movieNm"] = selected_info["movieNm"]

# Plotly에 전달할 마우스 오버 데이터
hover_data = trend[
    [
        "movieNm",
        "weekNum",
        "weekStartLabel",
        "rankLabel",
        "audienceLabel",
    ]
].to_numpy()

# 결측값은 None으로 전달
audiences = [
    int(x) if pd.notna(x) else None
    for x in trend["audiCnt"]
]

line_fig = go.Figure()

line_fig.add_trace(
    go.Scatter(
        x=trend["weekNum"],
        y=audiences,
        mode="lines+markers",
        connectgaps=False,
        customdata=hover_data,
        hovertemplate=(
            "영화 이름: %{customdata[0]}<br>"
            "주차: %{customdata[1]}주차<br>"
            "주 시작일: %{customdata[2]}<br>"
            "순위: %{customdata[3]}<br>"
            "주간 관객 수: %{customdata[4]}"
            "<extra></extra>"
        ),
        name=str(selected_info["movieNm"]),
    )
)

line_fig.update_layout(
    title=(
        f"{selected_info['movieNm']} | "
        "개봉 후 첫 20주 관객 수"
    ),
    xaxis_title="개봉 후 주차",
    yaxis_title="주간 관객 수(명)",
    xaxis=dict(
        tickmode="linear",
        tick0=1,
        dtick=1,
        range=[0.5, 20.5],
    ),
    yaxis_tickformat=",",
    height=480,
)

st.plotly_chart(
    line_fig,
    use_container_width=True,
)

missing_weeks = int(trend["audiCnt"].isna().sum())

st.caption(
    f"개봉 후 첫 20주 중 관객 수 기록이 없는 주: "
    f"{missing_weeks}주. "
    "기록이 없는 구간은 선으로 연결하지 않았습니다."
)


# ----------------------------------------
# 15. 분석 결과 안내
# ----------------------------------------
st.info(
    "이 결과는 역주행이 확정된 영화 목록이 아닙니다. "
    "개봉 초반보다 이후 주간 관객 수가 많아진 영화를 "
    "정해 둔 조건에 따라 선별한 후보 목록입니다."
)

st.warning(
    "KOBIS 주간 박스오피스 API에는 매주 상위 10편만 "
    "포함됩니다. 기록이 없는 주는 관객 수가 0이라는 "
    "뜻이 아닙니다. 순위권 밖에 있었거나 "
    "API 요청에 실패한 경우일 수 있습니다."
)

st.caption(
    "데이터 수집 시각: "
    f"{saved['loaded_at'].strftime('%Y-%m-%d %H:%M:%S')} "
    "(한국 시간)"
)
