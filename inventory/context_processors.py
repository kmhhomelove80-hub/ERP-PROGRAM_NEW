from .menu_permissions import menu_permissions, MENU_LABELS


def business_access(request):
    rights = menu_permissions(request.user)
    pending = 0
    if rights['inventory']:
        from .models import MovementRequest
        pending = MovementRequest.objects.filter(state='pending').count()
    from .inbound_work import can_record
    return {
        'can_record_inbound':can_record(request.user),
        'menu_permissions': rights,
        'can_write_current': rights.get(getattr(request, 'business_menu', ''), False),
        'read_only_employee': not any(rights.values()),
        'menu_access_summary': ' · '.join(f'{label}: {"모두" if rights[key] else "조회"}' for key, label in MENU_LABELS.items()),
        'movement_pending_count': pending,
    }
