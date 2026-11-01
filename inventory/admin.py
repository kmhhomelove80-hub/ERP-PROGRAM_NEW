from django.contrib import admin
from .models import Item, Stock, Allocation, MovementRequest, AuditEvent, ImportBatch, Receipt, PickingOrder, ErpSnapshot, ReconciliationRow, LegacyArchiveBatch, LegacyArchiveRecord, Substitution, SubstitutionSettlement
@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ['code','name']
    def has_module_permission(self, request): return request.user.is_active and request.user.is_staff
    search_fields = ['code','name']
    def has_view_permission(self, request, obj=None): return request.user.is_active and request.user.is_staff
    def has_add_permission(self, request): return request.user.is_active and request.user.is_staff
    def has_change_permission(self, request, obj=None): return request.user.is_active and request.user.is_staff
    def has_delete_permission(self, request, obj=None): return request.user.is_active and request.user.is_staff
    def save_model(self,request,obj,form,change):
        from .locking import warehouse_lock
        with warehouse_lock():super().save_model(request,obj,form,change)
    def get_readonly_fields(self,request,obj=None):
        return ['code'] if obj is not None and obj.stock_set.exists() else []
# Transactional records are read-only here; mutations must use audited services.
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request): return request.user.is_active and request.user.is_staff
    def has_view_permission(self, request, obj=None): return request.user.is_active and request.user.is_staff
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
for model in [Stock, Allocation, MovementRequest, AuditEvent, ImportBatch, Receipt, PickingOrder, ErpSnapshot, ReconciliationRow, LegacyArchiveBatch, LegacyArchiveRecord, Substitution, SubstitutionSettlement]:
    admin.site.register(model, ReadOnlyAdmin)

# Replace Django's many permission controls with one business-role selector.
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

FULL = 'full'
READ = 'read'
ROLE_CHOICES = [(FULL, '모든 권한 (읽기 / 쓰기 / 편집)'), (READ, '조회 권한 (읽기 전용)')]


class BusinessRoleMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['role'].initial = FULL if self.instance.is_staff else READ
            if self.instance.is_superuser:
                self.fields['role'].initial = FULL
                self.fields['role'].disabled = True
                self.fields['role'].help_text = '기존 시스템 책임자 계정의 권한은 유지됩니다.'

    def clean(self):
        data = super().clean()
        actor = getattr(self, '_actor', None)
        if actor and self.instance.pk == actor.pk:
            if data.get('role') != FULL or not data.get('is_active', True):
                raise forms.ValidationError('현재 로그인한 본인 계정의 권한을 낮추거나 비활성화할 수 없습니다. 다른 책임자에게 요청하세요.')
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = user.is_superuser or self.cleaned_data['role'] == FULL
        if commit:
            user.save()
            self.save_m2m()
        return user


class BusinessUserChangeForm(BusinessRoleMixin, UserChangeForm):
    role = forms.ChoiceField(label='사용 권한', choices=ROLE_CHOICES, widget=forms.RadioSelect)


class BusinessUserCreationForm(BusinessRoleMixin, UserCreationForm):
    role = forms.ChoiceField(label='사용 권한', choices=ROLE_CHOICES, initial=READ, widget=forms.RadioSelect)


User = get_user_model()
admin.site.unregister(User)


class BusinessRoleFilter(admin.SimpleListFilter):
    title = '사용 권한'
    parameter_name = 'business_role'

    def lookups(self, request, model_admin):
        return [(FULL, '모든 권한'), (READ, '조회 전용')]

    def queryset(self, request, queryset):
        if self.value() in (FULL, READ):
            return queryset.filter(is_staff=self.value() == FULL)
        return queryset


@admin.register(User)
class BusinessUserAdmin(UserAdmin):
    form = BusinessUserChangeForm
    add_form = BusinessUserCreationForm
    fieldsets = (
        ('로그인 정보', {'fields': ('username', 'password')}),
        ('직원 정보', {'fields': ('first_name', 'last_name', 'email')}),
        ('사용 설정', {'fields': ('role', 'is_active')}),
    )
    add_fieldsets = ((None, {'classes': ('wide',), 'fields': ('username', 'password1', 'password2', 'role')}),)
    list_display = ('username', 'first_name', 'business_role', 'is_active')
    list_filter = (BusinessRoleFilter, 'is_active')
    filter_horizontal = ()
    actions = None

    @admin.display(description='사용 권한')
    def business_role(self, obj):
        return '모든 권한' if obj.is_staff else '조회 전용'

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        class ActorForm(form):
            _actor = request.user
        return ActorForm

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_change_permission(self, request, obj=None):
        return (request.user.is_active and request.user.is_staff
                and (obj is None or not obj.is_superuser or request.user.is_superuser))

    def has_delete_permission(self, request, obj=None):
        return False  # Preserve employee attribution; deactivate departed users instead.

# Group-level permission pickers are not part of the two-role interface.
from django.contrib.auth.models import Group
admin.site.unregister(Group)

admin.site.site_header = "선일일렉콤 직원·품목 관리"
admin.site.site_title = "선일일렉콤 관리"
admin.site.index_title = "관리 메뉴"
