from django.contrib import admin
from .models import Item, Stock, Allocation, MovementRequest, AuditEvent, ImportBatch, Receipt, PickingOrder, ErpSnapshot, ReconciliationRow, LegacyArchiveBatch, LegacyArchiveRecord, Substitution, SubstitutionSettlement
@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ['code','name']
    def has_module_permission(self, request): return can_write(request.user, 'management')
    search_fields = ['code','name']
    def has_view_permission(self, request, obj=None): return can_write(request.user, 'management')
    def has_add_permission(self, request): return can_write(request.user, 'management')
    def has_change_permission(self, request, obj=None): return can_write(request.user, 'management')
    def has_delete_permission(self, request, obj=None): return can_write(request.user, 'management')
    def save_model(self,request,obj,form,change):
        from .locking import warehouse_lock
        with warehouse_lock():super().save_model(request,obj,form,change)
    def get_readonly_fields(self,request,obj=None):
        return ['code'] if obj is not None and obj.stock_set.exists() else []
# Transactional records are read-only here; mutations must use audited services.
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request): return can_write(request.user, 'management')
    def has_view_permission(self, request, obj=None): return can_write(request.user, 'management')
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
for model in [Stock, Allocation, MovementRequest, AuditEvent, ImportBatch, Receipt, PickingOrder, ErpSnapshot, ReconciliationRow, LegacyArchiveBatch, LegacyArchiveRecord, Substitution, SubstitutionSettlement]:
    admin.site.register(model, ReadOnlyAdmin)

# One selector per visible top-level business menu; no permission-code picker.
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.db import transaction
from .menu_permissions import MENU_LABELS, ACCESS_CHOICES, FULL, READ, can_write, menu_permissions
from .models import EmployeeMenuAccess


class MenuAccessMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rights = menu_permissions(self.instance) if self.instance.pk else {}
        for key, label in MENU_LABELS.items():
            field = self.fields[key + '_access']
            field.initial = FULL if rights.get(key) else READ
            if self.instance.is_superuser:
                field.initial = FULL
                field.disabled = True
                field.help_text = '시스템 책임자 계정은 모든 권한을 유지합니다.'

    def clean(self):
        data = super().clean()
        actor = getattr(self, '_actor', None)
        if actor and self.instance.pk == actor.pk:
            if data.get('management_access') != FULL or not data.get('is_active', True):
                raise forms.ValidationError('현재 로그인한 본인 계정의 권한을 낮추거나 비활성화할 수 없습니다. 다른 책임자에게 요청하세요.')
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        values = {key: self.cleaned_data[key + '_access'] for key in MENU_LABELS}
        user.is_staff = user.is_superuser or values['management'] == FULL
        user._pending_menu_access = values
        if commit:
            with transaction.atomic():
                user.save()
                EmployeeMenuAccess.objects.update_or_create(user=user, defaults=values)
                self.save_m2m()
        return user


class BusinessUserChangeForm(MenuAccessMixin, UserChangeForm):
    pass


class BusinessUserCreationForm(MenuAccessMixin, UserCreationForm):
    pass


for form_class in (BusinessUserChangeForm, BusinessUserCreationForm):
    for key, label in MENU_LABELS.items():
        field = forms.ChoiceField(label=label, choices=ACCESS_CHOICES, initial=READ)
        form_class.base_fields[key + '_access'] = field
        form_class.declared_fields[key + '_access'] = field


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class BusinessUserAdmin(UserAdmin):
    form = BusinessUserChangeForm
    add_form = BusinessUserCreationForm
    fieldsets = (
        ('로그인 정보', {'fields': ('username', 'password')}),
        ('직원 정보', {'fields': ('first_name', 'last_name', 'email')}),
        ('대메뉴별 권한', {'fields': tuple(key + '_access' for key in MENU_LABELS),
                       'description': '선택한 대메뉴 안의 기능에만 적용됩니다. 재고 조회는 모든 직원이 이용합니다. 재고 관리의 모두 가능은 이동 승인·실행을, 관리의 모두 가능은 직원 계정·품목 관리를 포함합니다.'}),
        ('사용 설정', {'fields': ('is_active',)}),
    )
    add_fieldsets = ((None, {'classes': ('wide',), 'fields': ('username', 'password1', 'password2', *(key + '_access' for key in MENU_LABELS))}),)
    list_display = ('username', 'first_name', 'business_access', 'is_active')
    list_filter = ('is_active',)
    filter_horizontal = ()
    actions = None

    @admin.display(description='대메뉴별 권한')
    def business_access(self, obj):
        rights = menu_permissions(obj)
        return ' · '.join(f'{label}: {"모두" if rights[key] else "조회"}' for key, label in MENU_LABELS.items())

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        class ActorForm(form):
            _actor = request.user
        return ActorForm

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            EmployeeMenuAccess.objects.update_or_create(user=obj, defaults=obj._pending_menu_access)

    def has_module_permission(self, request):
        return can_write(request.user, 'management')

    def has_view_permission(self, request, obj=None):
        return can_write(request.user, 'management')

    def has_add_permission(self, request):
        return can_write(request.user, 'management')

    def has_change_permission(self, request, obj=None):
        return (can_write(request.user, 'management')
                and (obj is None or not obj.is_superuser or request.user.is_superuser))

    def has_delete_permission(self, request, obj=None):
        return False  # Preserve employee attribution; deactivate departed users.


from django.contrib.auth.models import Group
admin.site.unregister(Group)
admin.site.site_header = '선일일렉콤 직원·품목 관리'
admin.site.site_title = '선일일렉콤 관리'
admin.site.index_title = '관리 메뉴'
