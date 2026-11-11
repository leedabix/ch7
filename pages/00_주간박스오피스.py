
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st


# ----------------------------------------
# 1. 기본 설정
# ----------------------------------------
st.title("🎬 최근 52주 주간 박스오피스")
st.write(
    "KOBIS API를 이용해 월요일부터 일요일까지의 "
    "주간 박스오피스 데이터를 최대 52주치 수집합니다."
)

API_URL = (
    "https://www.kobis.or.kr/kobisopenapi/webservice/rest/"
    "boxoffice/searchWeeklyBoxOfficeList.json"
)

WEEK_COUNT = 52
MAX_REQUESTS = 104

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
        "스트림릿 클라우드의 앱 설정에서 "
        "Settings → Secrets를 열고 다음 내용을 등록해 주세요.\n\n"
        'KOBIS_KEY = "발급받은_API_키"\n\n'
        "설정을 저장한 뒤 앱을 다시 실행하세요."
    )
    st.stop()


# ----------------------------------------
# 3. 기준 날짜 선택
# ----------------------------------------
selected_date = st.date_input(
    "기준 날짜를 선택하세요.",
    value=yesterday,
    max_value=yesterday,
    help="선택한 날짜부터 7일씩 거슬러 올라가며 조회합니다.",
)

st.caption(
    "주간(월~일) 기준으로 조회하며, "
    "KOBIS가 반환한 실제 집계 기간으로 중복을 확인합니다."
)


# ----------------------------------------
# 4. 세션 상태 준비
# ----------------------------------------
STATE_KEY = "kobis_weekly_result"

if STATE_KEY not in st.session_state:
    st.session_state[STATE_KEY] = None


# ----------------------------------------
# 5. 주간 API 요청 함수
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
            return None, "JSON 응답을 읽을 수 없음"

        if not isinstance(data, dict):
            return None, "응답 형식이 올바르지 않음"

        if "faultInfo" in data or "error" in data:
            return None, "API 요청 거부 또는 요청 변수 오류"

        result = data.get("boxOfficeResult")

        if not isinstance(result, dict):
            return None, "박스오피스 응답 구조를 찾을 수 없음"

        if not isinstance(result.get("weeklyBoxOfficeList"), list):
            return None, "영화 목록 형식이 올바르지 않음"

        return result, None

    except requests.exceptions.Timeout:
        return None, "10초 이내에 서버 응답이 없음"

    except requests.exceptions.ConnectionError:
        return None, "서버 연결 실패"

    except requests.exceptions.RequestException:
        return None, "네트워크 요청 오류"


# ----------------------------------------
# 6. 집계 기간 확인 함수
# ----------------------------------------
def parse_show_range(show_range):
    # 예: 20261102~20261108
    dates = re.fullmatch(
        r"\s*(\d{8})\s*~\s*(\d{8})\s*",
        str(show_range),
    )

    if not dates:
        return None

    try:
        start = datetime.strptime(
            dates.group(1), "%Y%m%d"
        ).date()
        end = datetime.strptime(
            dates.group(2), "%Y%m%d"
        ).date()
    except ValueError:
        return None

    # 월요일부터 일요일까지인지 확인
    if (
        start.weekday() != 0
        or end.weekday() != 6
        or (end - start).days != 6
    ):
        return None

    return start, end


# ----------------------------------------
# 7. 52주 데이터 수집
# ----------------------------------------
if st.button("최근 52주 데이터 불러오기", type="primary"):
    # 이전 결과를 지우고 새로운 조회 시작
    st.session_state[STATE_KEY] = None

    all_movies = []
    collected_periods = set()
    failed_requests = []

    request_count = 0
    success_count = 0
    duplicate_period_count = 0
    empty_period_count = 0

    progress_bar = st.progress(0)
    status_text = st.empty()

    # 같은 집계 기간이 반환될 경우를 대비해
    # 최대 104번까지 이전 날짜를 확인
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

        success_count += 1

        show_range = result.get("showRange", "")
        period = parse_show_range(show_range)

        if period is None:
            failed_requests.append({
                "요청 날짜": target_date,
                "실패 이유": "집계 기간 형식이 올바르지 않음",
            })
            success_count -= 1
            continue

        start_date, end_date = period
        period_key = (start_date, end_date)

        # 이미 수집한 집계 기간이면 제외
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

    # 결과는 현재 사용자 세션에만 저장
    st.session_state[STATE_KEY] = {
        "movies": all_movies,
        "periods": sorted(collected_periods),
        "requests": request_count,
        "successes": success_count,
        "failures": failed_requests,
        "duplicate_periods": duplicate_period_count,
        "empty_periods": empty_period_count,
        "base_date": selected_date,
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
# 8. 수집 결과 확인
# ----------------------------------------
saved = st.session_state[STATE_KEY]

if saved is None:
    st.info("기준 날짜를 선택하고 불러오기 버튼을 클릭하세요.")
    st.stop()

periods = saved["periods"]
failures = saved["failures"]

st.subheader("데이터 수집 결과")

col1, col2, col3, col4 = st.columns(4)

col1.metric("요청 횟수", saved["requests"])
col2.metric("성공 횟수", saved["successes"])
col3.metric("실패 횟수", len(failures))
col4.metric("수집한 주", f"{len(periods)}/{WEEK_COUNT}")

st.caption(
    f"중복 집계 기간으로 제외한 응답: "
    f"{saved['duplicate_periods']}회"
)

if periods:
    first_start = periods[0][0]
    last_end = periods[-1][1]

    st.write(
        f"**실제로 불러온 집계 기간:** "
        f"{first_start} ~ {last_end}"
    )

    with st.expander("수집한 주별 집계 기간 확인"):
        period_df = pd.DataFrame(
            [
                {
                    "집계 시작일": start,
                    "집계 종료일": end,
                    "영화 목록": (
                        "비어 있음"
                        if not any(
                            parse_show_range(
                                movie.get("showRange", "")
                            ) == (start, end)
                            for movie in saved["movies"]
                        )
                        else "수집됨"
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
        f"{saved['empty_periods']}주 있습니다. "
        "이를 관객 수 0으로 계산하지 않습니다."
    )

if failures:
    st.warning(
        f"{len(failures)}개 요청을 처리하지 못했습니다. "
        "실패한 요청의 데이터는 0으로 대체하지 않았습니다."
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
        "불러온 영화 데이터가 없습니다. "
        "기준 날짜를 변경해 다시 조회해 보세요."
    )
    st.stop()

df = pd.DataFrame(saved["movies"])

# 필요한 항목이 누락되어도 앱이 중단되지 않도록 처리
required_columns = [
    "showRange",
    "movieCd",
    "movieNm",
    "openDt",
    "rank",
    "audiCnt",
    "audiAcc",
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

# 개봉일을 날짜 형식으로 변환
open_dates = pd.to_datetime(
    df["openDt"],
    format="%Y-%m-%d",
    errors="coerce",
)

date_failures = int(open_dates.isna().sum())
df["openDt"] = open_dates

# 집계 시작일과 종료일도 날짜 형식으로 변환
df["periodStart"] = pd.to_datetime(
    df["periodStart"], errors="coerce"
)
df["periodEnd"] = pd.to_datetime(
    df["periodEnd"], errors="coerce"
)

period_date_failures = int(
    df["periodStart"].isna().sum()
    + df["periodEnd"].isna().sum()
)

total_failures = (
    numeric_failures
    + date_failures
    + period_date_failures
)

st.subheader("데이터 변환 확인")

st.write(
    f"숫자 변환 실패: **{numeric_failures}개** / "
    f"날짜 변환 실패: **{date_failures + period_date_failures}개**"
)

st.write(f"변환하지 못한 값 합계: **{total_failures}개**")

st.caption(
    "빈 값이나 형식이 잘못된 값은 결측값으로 처리합니다. "
    "숫자 변환에 실패한 값을 임의로 0으로 바꾸지 않습니다."
)


# ----------------------------------------
# 11. 중복 데이터 확인
# ----------------------------------------
# 영화 코드는 숫자가 아닌 문자열로 유지
df["movieCd"] = df["movieCd"].astype("string").str.strip()

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
# 12. 최종 표 출력
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

st.caption(
    "KOBIS 주간 박스오피스 API는 "
    "집계 기간별 최대 10편의 영화 정보를 제공합니다. "
    "이 표는 영화별·주별 기록을 통합한 결과입니다."
)
