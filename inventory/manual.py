"""Read the bundled training manual; the web and PDF share the same release files."""
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
import re
import unicodedata
from django.conf import settings
from django.http import Http404
from django.utils.safestring import mark_safe


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def text_only(html):
    parser = PlainText()
    parser.feed(html)
    return ' '.join(' '.join(parser.parts).split())


def search_key(text):
    return ''.join(unicodedata.normalize('NFKC', text).casefold().split())


def manual_path(extension):
    return Path(settings.BASE_DIR) / 'docs' / 'manual' / ('SUNIL_ERP_Beginner_Manual.' + extension)


@lru_cache(maxsize=2)
def _chapters(path, modified):
    # Only HTML generated from the repository manual is trusted. User search text
    # is never inserted into this HTML or passed to mark_safe.
    document = Path(path).read_text(encoding='utf-8')
    body = document.split('<body>', 1)[1].split('</body>', 1)[0]
    chapters = []
    for fragment in re.split(r'(?=<h2>\d+\.\s)', body)[1:]:
        heading = re.match(r'<h2>(\d+)\.\s*(.*?)</h2>', fragment)
        number, title = heading.groups()
        title = text_only(title)
        topics = {'1':'로그인 계정 ID PW 비밀번호 권한 매뉴얼', '3':'재고 조회 검색 정렬 바코드 QR 카메라',
                  '5':'이동 요청 결과', '6':'입고 신규 품목 기존 품목 도착 확인', '7':'피킹 추가 피킹 미배정 추가 배정 부족',
                  '8':'출고 Release 예약 취소', '9':'이동 승인 거부', '10':'대체 출고 상환 빌림',
                  '11':'ERP 대조 이력 엑셀', '12':'오류 문제 해결', '13':'인수인계 점검표', '14':'교육 실습'}
        topic = search_key(title + ' ' + topics.get(number, ''))
        chapters.append({'number': int(number), 'id': 'manual-chapter-' + number,
                         'title': title, 'html': mark_safe(fragment),
                         'topic': topic, 'search_text': search_key(text_only(fragment)) + topic})
    if not chapters:
        raise ValueError('Manual chapters are missing')
    return tuple(chapters)


def manual_chapters():
    path = manual_path('html')
    try:
        return _chapters(str(path), path.stat().st_mtime_ns)
    except (OSError, ValueError, IndexError, AttributeError) as error:
        raise Http404('사용 매뉴얼 파일이 없습니다. 프로그램 배포 파일을 확인해 주세요.') from error
