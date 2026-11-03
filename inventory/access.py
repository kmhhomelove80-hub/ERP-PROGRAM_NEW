"""Enforce the same top-level permissions for forms and direct HTTP requests."""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from .menu_permissions import can_write, MENU_LABELS


def menu_access(menu, *, write=False):
    if menu not in MENU_LABELS:
        raise ValueError('Unknown business menu')
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_active:
                raise PermissionDenied
            request.business_menu = menu
            if (write or request.method not in ('GET', 'HEAD', 'OPTIONS')) and not can_write(request.user, menu):
                raise PermissionDenied(f'{MENU_LABELS[menu]}의 모두 가능 권한이 필요합니다.')
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def warehouse_access(view):
    return menu_access('management')(view)


def warehouse_write(view):
    return menu_access('management', write=True)(view)
