"""Two business roles: staff may write, other active users may only read."""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def warehouse_access(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active:
            raise PermissionDenied
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not request.user.is_staff:
            raise PermissionDenied('조회 전용 계정은 등록·수정·처리를 할 수 없습니다.')
        return view(request, *args, **kwargs)
    return wrapped


def warehouse_write(view):
    @warehouse_access
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied('모든 권한이 필요한 작업입니다.')
        return view(request, *args, **kwargs)
    return wrapped
