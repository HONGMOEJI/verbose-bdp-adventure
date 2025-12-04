import requests
from bs4 import BeautifulSoup
import json
import re
import time
from datetime import datetime, timedelta
import pandas as pd
import os
import argparse
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import warnings
warnings.filterwarnings('ignore')

'''
설정

- 섹션
100 정치
101 경제
102 사회

- 헤더

'''
NAVER_NEWS_SECTIONS = {
    '100': 'politics',
    '101': 'economy',
    '102': 'society'
}

HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'ko-KR,ko;q=0.9',
    'Connection': 'keep-alive',
    'Accept-Encoding': 'gzip, deflate',
    'Cache-Control': 'no-cache'
}

COMMENT_API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'ko-KR,ko;q=0.9'
}

# URL 및 파일 경로
NAVER_NEWS_LIST_URL = 'https://news.naver.com/main/list.naver'
NAVER_COMMENT_API_URL = 'https://apis.naver.com/commentBox/cbox/web_naver_list_jsonp.json'
ARTICLES_OUTPUT_FILE = 'data/articles.csv'
COMMENTS_OUTPUT_FILE = 'data/comments.csv'

# 성능 설정
MAX_ARTICLE_WORKERS = 8  # 기사 수집 워커 수
MAX_COMMENT_WORKERS = 20  # 댓글 수집 워커 수
TARGET_COMMENTS_PER_SECTION = 3000  # 섹션별 목표 댓글 수
ARTICLE_PROCESSING_BATCH_SIZE = 30  # 기사 처리 배치 크기
COMMENT_PROCESSING_BATCH_SIZE = 50  # 댓글 처리 배치 크기
MINIMUM_COMMENT_LENGTH = 15  # 최소 댓글 길이

# 딜레이 설정 (초)
ARTICLE_REQUEST_DELAY = 0.05
COMMENT_REQUEST_DELAY = 0.1
BATCH_PROCESSING_DELAY = 0.5

def setup_argument_parser():
    parser = argparse.ArgumentParser(description='네이버 뉴스 크롤러')
    parser.add_argument('--start-date', default='20241201', help='시작 날짜 (YYYYMMDD)')
    parser.add_argument('--end-date', default=datetime.now().strftime('%Y%m%d'), help='종료 날짜 (YYYYMMDD)')
    parser.add_argument('--max-retries', type=int, default=2, help='최대 재시도 횟수')
    parser.add_argument('--comment-pages', type=int, default=10, help='기사당 최대 댓글 페이지 수')
    return parser.parse_args()

args = setup_argument_parser()

START_DATE = datetime.strptime(args.start_date, '%Y%m%d')
END_DATE = datetime.strptime(args.end_date, '%Y%m%d')

# 중복 방지를 위한 전역 변수
existing_article_urls = set()
existing_comment_keys = set()
data_write_lock = threading.Lock()
progress_display_lock = threading.Lock()

# 진행 상황 추적
crawling_progress = {
    'total_articles_collected': 0,
    'total_comments_collected': 0,
    'current_processing_section': '',
    'section_progress_details': {}
}

def load_existing_data_from_files():
    """기존 데이터를 로드하여 중복 수집 방지"""
    global existing_article_urls, existing_comment_keys

    if os.path.exists(ARTICLES_OUTPUT_FILE):
        try:
            existing_articles_df = pd.read_csv(ARTICLES_OUTPUT_FILE)
            existing_article_urls = set(existing_articles_df['url'].astype(str))
            print(f'[DATA_LOADER] 기존 기사 {len(existing_article_urls)}개 로드 완료')
        except Exception as e:
            print(f'[DATA_LOADER] 기존 기사 로드 실패: {e}')

    if os.path.exists(COMMENTS_OUTPUT_FILE):
        try:
            existing_comments_df = pd.read_csv(COMMENTS_OUTPUT_FILE)
            existing_comment_keys = set(existing_comments_df['article_id'].astype(str) + '_' + existing_comments_df['author'].astype(str))
            print(f'[DATA_LOADER] 기존 댓글 {len(existing_comment_keys)}개 로드 완료')
        except Exception as e:
            print(f'[DATA_LOADER] 기존 댓글 로드 실패: {e}')

load_existing_data_from_files()

def initialize_csv_files():
    """CSV 파일 초기화"""
    if not os.path.exists(ARTICLES_OUTPUT_FILE):
        articles_df = pd.DataFrame(columns=['url', 'title', 'pub_datetime', 'section_code'])
        articles_df.to_csv(ARTICLES_OUTPUT_FILE, index=False, encoding='utf-8-sig')
        print(f'[FILE_MANAGER] 기사 CSV 파일 생성: {ARTICLES_OUTPUT_FILE}')

    if not os.path.exists(COMMENTS_OUTPUT_FILE):
        comments_df = pd.DataFrame(columns=['article_id', 'section_code', 'author', 'comment_date',
                                          'content', 'recommend_count', 'unrecommend_count'])
        comments_df.to_csv(COMMENTS_OUTPUT_FILE, index=False, encoding='utf-8-sig')
        print(f'[FILE_MANAGER] 댓글 CSV 파일 생성: {COMMENTS_OUTPUT_FILE}')

def save_articles_batch_to_csv(articles_data_list):
    """기사 데이터를 CSV 파일에 배치 저장"""
    if not articles_data_list:
        return

    with data_write_lock:
        try:
            articles_df = pd.DataFrame(articles_data_list)
            articles_df.to_csv(ARTICLES_OUTPUT_FILE, mode='a', header=False, index=False, encoding='utf-8-sig')
            crawling_progress['total_articles_collected'] += len(articles_data_list)
            print(f'[FILE_MANAGER] 기사 {len(articles_data_list)}개 저장 완료')
        except Exception as e:
            print(f'[FILE_MANAGER] 기사 저장 실패: {e}')

def save_comments_batch_to_csv(comments_data_list):
    """댓글 데이터를 CSV 파일에 배치 저장"""
    if not comments_data_list:
        return

    with data_write_lock:
        try:
            comments_df = pd.DataFrame(comments_data_list)
            comments_df.to_csv(COMMENTS_OUTPUT_FILE, mode='a', header=False, index=False, encoding='utf-8-sig')
            crawling_progress['total_comments_collected'] += len(comments_data_list)
            print(f'[FILE_MANAGER] 댓글 {len(comments_data_list)}개 저장 완료')
        except Exception as e:
            print(f'[FILE_MANAGER] 댓글 저장 실패: {e}')

def parse_korean_datetime_from_text(date_text_input):
    """네이버 뉴스 날짜 텍스트를 datetime 객체로 변환"""
    try:
        # 절대 날짜 형식: "2024.12.01. 오후 3:30"
        if re.match(r'\d{4}\.\d{2}\.\d{2}\.', date_text_input):
            date_parts = date_text_input.split('.')[0:3]
            date_string = '.'.join(date_parts)

            time_pattern_match = re.search(r'(오전|오후) (\d{1,2}):(\d{2})', date_text_input)
            if time_pattern_match:
                period, hour, minute = time_pattern_match.groups()
                hour = int(hour)
                # 오후 시간 변환
                if period == '오후' and hour != 12:
                    hour += 12
                elif period == '오전' and hour == 12:
                    hour = 0

                base_datetime = datetime.strptime(date_string, '%Y.%m.%d')
                return base_datetime.replace(hour=hour, minute=int(minute))

        current_time = datetime.now()

        # 상대 날짜 형식: "3일전", "5시간전", "10분전"
        if '일전' in date_text_input:
            days_ago = int(re.search(r'(\d+)일전', date_text_input).group(1))
            base_date = current_time - timedelta(days=days_ago)
            return base_date.replace(hour=random.randint(0, 23), minute=random.randint(0, 59), second=0, microsecond=0)
        elif '시간전' in date_text_input:
            hours_ago = int(re.search(r'(\d+)시간전', date_text_input).group(1))
            return current_time - timedelta(hours=hours_ago)
        elif '분전' in date_text_input:
            minutes_ago = int(re.search(r'(\d+)분전', date_text_input).group(1))
            return current_time - timedelta(minutes=minutes_ago)

        return None
    except Exception as e:
        print(f'[DATE_PARSER] 날짜 파싱 실패: {date_text_input}, 오류: {e}')
        return None

def extract_article_id_from_url(article_url):
    """기사 URL에서 ID 추출 (언론사ID/기사ID 형식)"""
    try:
        url_pattern_match = re.search(r'/article/(\d{3})/(\d+)', article_url)
        if url_pattern_match:
            press_id, article_id = url_pattern_match.groups()
            return f"{press_id}/{article_id}"
        return None
    except Exception as e:
        print(f'[URL_PARSER] 기사 ID 추출 실패: {article_url}, 오류: {e}')
        return None

def create_optimized_http_session():
    """재시도 전략과 연결 풀링이 적용된 HTTP 세션 생성"""
    http_session = requests.Session()
    retry_strategy = Retry(
        total=args.max_retries,
        backoff_factor=0.1,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    http_adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=100, pool_maxsize=100)
    http_session.mount("http://", http_adapter)
    http_session.mount("https://", http_adapter)
    http_session.headers.update(HTTP_HEADERS)
    http_session.timeout = (3, 10)
    return http_session

def collect_articles_from_news_section(date_string, section_id, target_date_obj):
    """특정 날짜의 특정 섹션에서 기사 수집"""
    http_session = create_optimized_http_session()
    collected_articles = []
    current_page = 1
    consecutive_failure_count = 0

    print(f'[ARTICLE_COLLECTOR] 섹션 {section_id} 기사 수집 시작 ({date_string})')

    # 연속 실패 2회 또는 최대 150페이지까지
    while consecutive_failure_count < 2 and current_page <= 150:
        try:
            request_params = {
                'mode': 'LS2D',
                'mid': 'shm',
                'sid1': section_id,
                'date': date_string,
                'page': current_page
            }

            time.sleep(ARTICLE_REQUEST_DELAY)
            response = http_session.get(NAVER_NEWS_LIST_URL, params=request_params)
            response.raise_for_status()

            page_soup = BeautifulSoup(response.text, 'html.parser')
            articles_found_on_page = 0

            # 페이지의 모든 기사 파싱
            for article_item in page_soup.select('dt'):
                try:
                    link_element = article_item.find('a', href=True)
                    if not link_element:
                        continue

                    article_url = link_element['href']
                    # 네이버 뉴스 기사 URL 패턴 검증
                    if not re.search(r'/article/(\d{3})/(\d+)', article_url):
                        continue

                    # 절대 URL 변환
                    article_url = article_url if article_url.startswith('http') else 'https:' + article_url

                    # 중복 체크
                    if article_url in existing_article_urls:
                        continue

                    article_title = link_element.get_text(strip=True)
                    if not article_title or len(article_title) < 5:
                        continue

                    # 날짜 정보 추출
                    date_element = article_item.find_next_sibling('dd')
                    if date_element:
                        date_span = date_element.find('span', class_='date')
                        if date_span:
                            date_text = date_span.get_text(strip=True)
                            parsed_datetime = parse_korean_datetime_from_text(date_text)

                            # 목표 날짜와 일치하는지 확인
                            if parsed_datetime and parsed_datetime.date() == target_date_obj.date():
                                article_id = extract_article_id_from_url(article_url)
                                if article_id:
                                    article_data = {
                                        'url': article_url,
                                        'title': article_title,
                                        'pub_datetime': parsed_datetime.isoformat(),
                                        'section_code': section_id,
                                        'article_id': article_id
                                    }
                                    collected_articles.append(article_data)
                                    existing_article_urls.add(article_url)
                                    articles_found_on_page += 1

                except Exception as e:
                    print(f'[ARTICLE_COLLECTOR] 기사 파싱 오류: {e}')
                    continue

            # 기사를 찾지 못한 경우 실패 카운트 증가
            if articles_found_on_page == 0:
                consecutive_failure_count += 1
            else:
                consecutive_failure_count = 0

            current_page += 1

        except Exception as e:
            print(f'[ARTICLE_COLLECTOR] 페이지 {current_page} 오류: {e}')
            consecutive_failure_count += 1

    http_session.close()
    print(f'[ARTICLE_COLLECTOR] 섹션 {section_id} 기사 {len(collected_articles)}개 수집 완료')
    return collected_articles

def collect_daily_articles_all_sections(date_string, target_date_obj):
    """모든 섹션에서 해당 날짜의 기사 수집 (병렬 처리)"""
    all_collected_articles = []

    print(f'[DAILY_COLLECTOR] {date_string} 일일 기사 수집 시작')

    # 섹션별 병렬 수집
    with ThreadPoolExecutor(max_workers=MAX_ARTICLE_WORKERS) as executor:
        section_tasks = [
            executor.submit(collect_articles_from_news_section, date_string, section_id, target_date_obj)
            for section_id in NAVER_NEWS_SECTIONS.keys()
        ]

        for completed_task in as_completed(section_tasks):
            try:
                section_articles = completed_task.result()
                all_collected_articles.extend(section_articles)
            except Exception as e:
                print(f'[DAILY_COLLECTOR] 섹션 작업 실패: {e}')

    # CSV에 저장 (article_id는 댓글 수집용이므로 저장하지 않음)
    if all_collected_articles:
        articles_for_csv = [
            {
                'url': article['url'],
                'title': article['title'],
                'pub_datetime': article['pub_datetime'],
                'section_code': article['section_code']
            }
            for article in all_collected_articles
        ]
        save_articles_batch_to_csv(articles_for_csv)

    print(f'[DAILY_COLLECTOR] {date_string} 총 기사 {len(all_collected_articles)}개 수집 완료')
    return all_collected_articles

def validate_comment_quality(comment_content, comment_author):
    """댓글 품질 검증 (삭제된 댓글, 짧은 댓글, 무의미한 패턴 필터링)"""
    if not comment_content or not comment_author:
        return False

    # 삭제된 댓글 체크
    if '삭제된 댓글' in comment_content or '=====' in comment_content:
        return False

    # 최소 길이 체크
    if len(comment_content.strip()) < MINIMUM_COMMENT_LENGTH:
        return False

    # 무의미한 패턴 체크
    meaningless_patterns = [
        r'^[ㅋㅎㅜㅠ]+$',  # 자음/모음만
        r'^[.]{3,}$',      # 점 반복
        r'^[!?]{3,}$',     # 특수문자 반복
        r'^[0-9\s]+$'      # 숫자만
    ]

    for pattern in meaningless_patterns:
        if re.match(pattern, comment_content.strip()):
            return False

    return True

def fetch_comments_via_naver_api(article_id, section_code, article_title, article_url):
    """네이버 댓글 API를 통해 기사의 댓글 수집"""
    press_id, news_id = article_id.split('/')

    # 섹션별 템플릿 매핑
    section_template_mapping = {
        '100': 'default_politics',
        '101': 'default_economy',
        '102': 'default_society',
        '103': 'default_life',
        '104': 'default_world'
    }

    template_id = section_template_mapping.get(section_code, 'default')

    # API 기본 파라미터 설정
    api_base_params = {
        'ticket': 'news',
        'templateId': template_id,
        'pool': 'cbox5',
        '_cv': datetime.now().strftime('%Y%m%d%H%M%S'),
        'lang': 'ko',
        'country': 'KR',
        'objectId': f'news{press_id},{news_id}',
        'pageSize': 100,
        'indexSize': 10,
        'listType': 'OBJECT',
        'pageType': 'more',
        'refresh': 'false',
        'sort': 'NEW',
        'followSize': 100,
        'includeAllStatus': 'true',
        '_': int(time.time() * 1000)
    }

    request_headers = COMMENT_API_HEADERS.copy()
    request_headers['Referer'] = article_url

    collected_comments = []
    http_session = create_optimized_http_session()
    http_session.headers.update(request_headers)

    try:
        current_page = 1
        while current_page <= args.comment_pages:
            current_api_params = api_base_params.copy()
            current_api_params['page'] = current_page

            time.sleep(COMMENT_REQUEST_DELAY)

            try:
                api_response = http_session.get(NAVER_COMMENT_API_URL, params=current_api_params, timeout=5)

                if api_response.status_code != 200:
                    break

                response_text = api_response.text.strip()

                # JSONP 응답 파싱
                if response_text.startswith('_callback('):
                    json_content = response_text[10:-2]
                elif '(' in response_text and response_text.endswith(');'):
                    start_index = response_text.find('(') + 1
                    json_content = response_text[start_index:-2]
                else:
                    json_content = response_text

                try:
                    api_data = json.loads(json_content)
                except Exception as e:
                    print(f'[COMMENT_API] JSON 파싱 실패 (기사 {article_id}): {e}')
                    break

                # API 응답 성공 여부 확인
                if not api_data.get('success', False):
                    # 템플릿 오류 시 기본 템플릿으로 재시도
                    if 'Wrong ticket' in api_data.get('message', ''):
                        current_api_params['templateId'] = 'default'
                        time.sleep(0.1)
                        retry_response = http_session.get(NAVER_COMMENT_API_URL, params=current_api_params, timeout=5)
                        if retry_response.status_code == 200:
                            retry_text = retry_response.text.strip()
                            if retry_text.startswith('_callback('):
                                retry_json = retry_text[10:-2]
                            elif '(' in retry_text and retry_text.endswith(');'):
                                start = retry_text.find('(') + 1
                                retry_json = retry_text[start:-2]
                            else:
                                retry_json = retry_text

                            try:
                                retry_data = json.loads(retry_json)
                                if retry_data.get('success', False):
                                    api_data = retry_data
                                else:
                                    break
                            except:
                                break
                        else:
                            break
                    else:
                        break

                # 댓글 데이터 추출
                page_comments = extract_comments_from_api_response(api_data, article_id, section_code)

                if not page_comments:
                    break

                collected_comments.extend(page_comments)
                current_page += 1

            except Exception as e:
                print(f'[COMMENT_API] 기사 {article_id} 페이지 {current_page} 요청 실패: {e}')
                break

    finally:
        http_session.close()

    print(f'[COMMENT_API] 기사 {article_id} 댓글 {len(collected_comments)}개 수집 완료')
    return collected_comments

def extract_comments_from_api_response(api_response_data, article_id, section_code):
    """API 응답에서 댓글 데이터 추출 및 정제"""
    processed_comments = []
    try:
        if not isinstance(api_response_data, dict) or 'result' not in api_response_data:
            return processed_comments

        result_data = api_response_data['result']
        if not isinstance(result_data, dict) or 'commentList' not in result_data:
            return processed_comments

        comment_list = result_data['commentList']
        if not isinstance(comment_list, list):
            return processed_comments

        for comment_data in comment_list:
            if not isinstance(comment_data, dict):
                continue

            # 작성자 이름 추출
            author_name = comment_data.get('userName', '').strip()
            if not author_name:
                author_name = comment_data.get('maskedUserName', '').strip()

            # 댓글 내용 및 메타데이터 추출
            comment_content = comment_data.get('contents', '').strip()
            registration_time = comment_data.get('regTime', '').strip()

            recommend_count = comment_data.get('sympathyCount', 0) or 0
            unrecommend_count = comment_data.get('antipathyCount', 0) or 0

            # 품질 검증
            if not validate_comment_quality(comment_content, author_name):
                continue

            # 날짜 포맷팅
            formatted_date = ''
            if registration_time:
                try:
                    parsed_datetime = datetime.fromisoformat(registration_time.replace('+0900', '+09:00'))
                    formatted_date = parsed_datetime.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    formatted_date = registration_time

            # 텍스트 정제 (특수문자 제거)
            cleaned_content = re.sub(r'[^\w\s가-힣ㄱ-ㅎㅏ-ㅣ.,!?~ᆢ]', '', comment_content)
            cleaned_content = cleaned_content.replace('\n', ' ').strip()

            # 정제 후 재검증
            if not validate_comment_quality(cleaned_content, author_name):
                continue

            # 중복 체크 (기사ID_작성자_날짜)
            comment_unique_key = f"{article_id}_{author_name}_{formatted_date}"
            if comment_unique_key in existing_comment_keys:
                continue

            comment_record = {
                'article_id': article_id,
                'section_code': section_code,
                'author': author_name,
                'comment_date': formatted_date,
                'content': cleaned_content,
                'recommend_count': str(recommend_count),
                'unrecommend_count': str(unrecommend_count)
            }

            processed_comments.append(comment_record)
            existing_comment_keys.add(comment_unique_key)

    except Exception as e:
        print(f'[COMMENT_PROCESSOR] 기사 {article_id} 댓글 처리 오류: {e}')

    return processed_comments

def collect_comments_for_news_section(section_code, section_articles, target_comment_count):
    """특정 섹션의 댓글을 목표 개수만큼 수집"""
    section_name = NAVER_NEWS_SECTIONS[section_code]

    if not section_articles:
        print(f'[SECTION_COLLECTOR] 섹션 {section_name}에 사용 가능한 기사가 없습니다')
        return 0

    current_comment_count = 0
    available_articles = section_articles.copy()
    random.shuffle(available_articles)  # 기사 순서 무작위화

    # 진행 상황 초기화
    with progress_display_lock:
        crawling_progress['section_progress_details'][section_code] = {
            'current_count': 0,
            'target_count': target_comment_count,
            'section_name': section_name
        }

    print(f'[SECTION_COLLECTOR] {section_name} 댓글 수집 시작, 목표: {target_comment_count}')

    while current_comment_count < target_comment_count and available_articles:
        # 동적 배치 크기 조정
        remaining_needed = target_comment_count - current_comment_count
        batch_size = min(COMMENT_PROCESSING_BATCH_SIZE, len(available_articles), max(10, remaining_needed // 10))

        current_batch_articles = available_articles[:batch_size]
        available_articles = available_articles[batch_size:]

        # 기사가 부족하면 재활용
        if len(available_articles) < 10 and current_comment_count < target_comment_count:
            available_articles.extend(section_articles)
            random.shuffle(available_articles)

        batch_comments = []
        # 배치 내 기사들을 병렬로 처리
        with ThreadPoolExecutor(max_workers=MAX_COMMENT_WORKERS) as executor:
            comment_tasks = [
                executor.submit(fetch_comments_via_naver_api, article['article_id'],
                              section_code, article['title'], article['url'])
                for article in current_batch_articles
            ]

            for completed_task in as_completed(comment_tasks):
                try:
                    article_comments = completed_task.result()
                    if article_comments:
                        # 목표 개수 초과 방지
                        remaining_slots = target_comment_count - current_comment_count
                        if remaining_slots > 0:
                            comments_to_add = article_comments[:remaining_slots]
                            batch_comments.extend(comments_to_add)
                            current_comment_count += len(comments_to_add)

                            if current_comment_count >= target_comment_count:
                                break

                except Exception as e:
                    print(f'[SECTION_COLLECTOR] 댓글 작업 실패: {e}')

        # 배치 저장 및 진행률 업데이트
        if batch_comments:
            save_comments_batch_to_csv(batch_comments)

            with progress_display_lock:
                crawling_progress['section_progress_details'][section_code]['current_count'] = current_comment_count
                progress_percentage = (current_comment_count / target_comment_count) * 100
                print(f'[SECTION_COLLECTOR] {section_name}: {current_comment_count}/{target_comment_count} ({progress_percentage:.1f}%)')

        if current_comment_count >= target_comment_count:
            break

        time.sleep(BATCH_PROCESSING_DELAY)

    print(f'[SECTION_COLLECTOR] {section_name} 완료: 댓글 {current_comment_count}개 수집')
    return current_comment_count

def collect_daily_comments_all_sections(date_string, daily_articles):
    """모든 섹션에서 댓글 수집 (섹션별 병렬 처리)"""
    # 섹션별로 기사 분류
    articles_by_section = defaultdict(list)
    for article in daily_articles:
        articles_by_section[article['section_code']].append(article)

    total_comments_collected = 0

    print(f'[DAILY_COMMENT_COLLECTOR] {date_string} 댓글 수집 시작')

    # 섹션별 병렬 수집
    with ThreadPoolExecutor(max_workers=len(NAVER_NEWS_SECTIONS)) as executor:
        section_tasks = []
        for section_code in NAVER_NEWS_SECTIONS.keys():
            if section_code in articles_by_section:
                task = executor.submit(
                    collect_comments_for_news_section,
                    section_code,
                    articles_by_section[section_code],
                    TARGET_COMMENTS_PER_SECTION
                )
                section_tasks.append(task)

        for completed_task in as_completed(section_tasks):
            try:
                section_comment_count = completed_task.result()
                total_comments_collected += section_comment_count
            except Exception as e:
                print(f'[DAILY_COMMENT_COLLECTOR] 섹션 작업 실패: {e}')

    print(f'[DAILY_COMMENT_COLLECTOR] {date_string} 총 댓글 {total_comments_collected}개 수집 완료')
    return total_comments_collected

def display_current_progress_summary():
    """현재 크롤링 진행 상황 출력"""
    total_articles = crawling_progress['total_articles_collected']
    total_comments = crawling_progress['total_comments_collected']
    print(f'\n[PROGRESS_TRACKER] 현재 상태: 기사 {total_articles}개, 댓글 {total_comments}개 수집')

    if crawling_progress['section_progress_details']:
        for section_code, progress_info in crawling_progress['section_progress_details'].items():
            current = progress_info['current_count']
            target = progress_info['target_count']
            name = progress_info['section_name']
            progress_percent = (current / target * 100) if target > 0 else 0
            print(f'[PROGRESS_TRACKER] {name}: {current}/{target} ({progress_percent:.1f}%)')

def execute_naver_news_crawler():
    """메인 크롤러 실행 함수"""
    print('[MAIN] 네이버 뉴스 크롤러 시작')
    print(f'[MAIN] 수집 기간: {START_DATE.date()} ~ {END_DATE.date()}')
    print(f'[MAIN] 대상 섹션: {", ".join(NAVER_NEWS_SECTIONS.values())} ({len(NAVER_NEWS_SECTIONS)}개)')
    print(f'[MAIN] 섹션별 일일 댓글 목표: {TARGET_COMMENTS_PER_SECTION}개')
    print(f'[MAIN] 댓글 워커: {MAX_COMMENT_WORKERS} 스레드')
    print(f'[MAIN] 최소 댓글 길이: {MINIMUM_COMMENT_LENGTH}자')

    initialize_csv_files()

    processed_date_count = 0
    total_date_count = (END_DATE - START_DATE).days + 1

    current_processing_date = START_DATE
    while current_processing_date <= END_DATE:
        date_string = current_processing_date.strftime('%Y%m%d')

        print(f'\n{"="*50}')
        print(f'[MAIN] 처리 날짜: {date_string} ({processed_date_count+1}/{total_date_count})')
        print(f'{"="*50}')

        try:
            # 1. 기사 수집
            daily_articles = collect_daily_articles_all_sections(date_string, current_processing_date)

            # 2. 댓글 수집
            if daily_articles:
                comments_collected = collect_daily_comments_all_sections(date_string, daily_articles)

            processed_date_count += 1

            # 진행 상황 출력
            overall_progress_percent = (processed_date_count / total_date_count) * 100
            print(f'\n[MAIN] 전체 진행률: {processed_date_count}/{total_date_count} ({overall_progress_percent:.1f}%)')
            display_current_progress_summary()

            # 날짜 간 대기
            if current_processing_date < END_DATE:
                time.sleep(1)

        except KeyboardInterrupt:
            print('\n[MAIN] 사용자에 의해 중단됨')
            break
        except Exception as e:
            print(f'[MAIN] {date_string} 처리 중 오류: {e}')

        current_processing_date += timedelta(days=1)

    # 최종 결과 출력
    print(f'\n{"="*50}')
    print(f'[MAIN] 크롤링 완료!')
    print(f'{"="*50}')
    print(f'[MAIN] 기간: {START_DATE.date()} ~ {END_DATE.date()}')
    print(f'[MAIN] 총 기사: {crawling_progress["total_articles_collected"]}개')
    print(f'[MAIN] 총 댓글: {crawling_progress["total_comments_collected"]}개')

    if processed_date_count > 0:
        avg_articles = crawling_progress["total_articles_collected"] / processed_date_count
        avg_comments = crawling_progress["total_comments_collected"] / processed_date_count
        print(f'[MAIN] 일평균: 기사 {avg_articles:.0f}개, 댓글 {avg_comments:.0f}개')

    print(f'[MAIN] 출력 파일: {ARTICLES_OUTPUT_FILE}, {COMMENTS_OUTPUT_FILE}')
    print(f'[MAIN] 동시 스레드: {MAX_COMMENT_WORKERS}개 활용')

if __name__ == '__main__':
    execute_naver_news_crawler()
