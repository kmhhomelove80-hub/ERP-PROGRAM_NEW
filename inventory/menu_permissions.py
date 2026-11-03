"""Independent top-level business permissions; read access remains available."""
from django.core.exceptions import ValidationError

MENU_LABELS = {
    'receiving': '입고 관리', 'shipping': '출고 관리', 'production': '생산계획조회',
    'inventory': '재고 관리', 'management': '관리',
}
READ = 'read'
FULL = 'full'
ACCESS_CHOICES = [(READ, '조회만 가능'), (FULL, '모두 가능')]


def menu_permissions(user):
    if not user or not user.is_authenticated or not user.is_active:
        return {key: False for key in MENU_LABELS}
    if user.is_superuser:
        return {key: True for key in MENU_LABELS}
    from .models import EmployeeMenuAccess
    profile = EmployeeMenuAccess.objects.filter(user_id=user.pk).values(*MENU_LABELS).first()
    if profile is None:
        # Existing staff/worker accounts keep their current rights until migrated
        # or explicitly configured. New accounts made by the UI save all fields.
        return {key: bool(user.is_staff) for key in MENU_LABELS}
    rights = {key: profile[key] == FULL for key in MENU_LABELS}
    rights['management'] = rights['management'] and bool(user.is_staff)
    return rights


def can_write(user, menu):
    return menu_permissions(user).get(menu, False)


def require_menu_write(user, menu):
    if not can_write(user, menu):
        raise ValidationError(f'{MENU_LABELS[menu]}의 모두 가능 권한이 필요합니다.')
