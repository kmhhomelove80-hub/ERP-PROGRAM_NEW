from inventory import stock_filters
from django.contrib.auth.decorators import login_required
from inventory import company_print_views
from inventory import allocation_filters
from inventory.access import menu_access
from django.views.decorators.http import require_GET
from inventory import inbound_work_views
from inventory import cleanup_views
from inventory import stocktake_views
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from inventory import views, production_views, inbound_views
from .auth_views import WarehouseLoginView
urlpatterns = [
    path('stock/options/', login_required(require_GET(stock_filters.options)), name='stock-options'),
    path('company-print/',company_print_views.jobs,name='company-print-jobs'),
    path('company-print/settings/',company_print_views.settings,name='company-printer-settings'),
    path('picking/<uuid:pk>/company-print/',company_print_views.order_print,name='company-print-preview'),
    path('allocations/options/',menu_access('shipping')(require_GET(allocation_filters.options)),name='allocation-options'),
    path('inbound/saved/',inbound_work_views.index,name='inbound-work-index'),
    path('inbound/saved/<uuid:pk>/',inbound_work_views.detail,name='inbound-work-detail'),
    path('inbound/saved/<uuid:pk>/row/',inbound_work_views.save_row,name='inbound-work-save-row'),
    path('stocktake/',stocktake_views.index,name='stocktake-index'),
    path('stocktake/adjust/',stocktake_views.adjust,name='stocktake-adjust'),
    path('stocktake/<uuid:pk>/',stocktake_views.detail,name='stocktake-detail'),
    path('stocktake/<uuid:pk>/download/',stocktake_views.download,name='stocktake-download'),
    path('stocktake/<uuid:pk>/source/',stocktake_views.source,name='stocktake-source'),
    path('inbound/',inbound_views.inbound_list,name='inbound-list'),
    path('inbound/locations/',inbound_views.inbound_locations,name='inbound-locations'),
    path('notifications/movements/',views.movement_notifications,name='movement-notifications'),
    path('production/',production_views.production_plan,name='production-plan'),
    path('production/settings/',production_views.production_settings,name='production-settings'),
    path('admin/',admin.site.urls),
    path('login/',WarehouseLoginView.as_view(),name='login'),
    path('logout/',auth_views.LogoutView.as_view(),name='logout'),
    path('',views.stock_list,name='stock-list'),
    path('manual/',views.user_manual,name='user-manual'),
    path('manual/pdf/',views.user_manual_pdf,name='user-manual-pdf'),
    path('picking/',views.picking_list,name='picking-list'),
    path('allocation-history/',views.allocation_history,name='allocation-history'),
    path('picking/new/',views.picking_create,name='picking-create'),
    path('picking/<uuid:pk>/supplement/',views.picking_supplement,name='picking-supplement'),
    path('picking/<uuid:pk>/substitute/',views.substitution_create,name='substitution-create'),
    path('substitutions/',views.substitution_list,name='substitution-list'),
    path('substitutions/<uuid:pk>/',views.substitution_detail,name='substitution-detail'),
    path('picking/<uuid:pk>/customer/',views.customer_picking_print,name='customer-picking-print'),
    path('picking/<uuid:pk>/',views.picking_detail,name='picking-detail'),
    path('allocations/<uuid:pk>/reassign/',views.allocation_reassign,name='allocation-reassign'),
    path('cleanup/keep/',cleanup_views.keep,name='cleanup-keep'),
    path('cleanup/decisions/',cleanup_views.decisions,name='cleanup-decisions'),
    path('cleanup/decisions/<int:pk>/cancel/',cleanup_views.cancel,name='cleanup-cancel'),
    path('cleanup/',views.cleanup_list,name='cleanup-list'),
    path('allocations/',views.allocation_list,name='allocation-list'),
    path('movements/',views.movement_list,name='movement-list'),
    path('movements/<uuid:pk>/',views.movement_detail,name='movement-detail'),
    path('stock/export/',views.stock_export,name='stock-export'),
    path('stock/<int:pk>/allocations/',views.stock_allocations,name='stock-allocations'),
    path('stock/<int:pk>/move/',views.movement_create,name='movement-create'),
    path('archives/',views.archive_list,name='archive-list'),
    path('operations/',views.operations_status,name='operations-status'),
    path('history/',views.history_list,name='history-list'),
    path('erp/',views.erp_list,name='erp-list'),
    path('erp/upload/',views.erp_upload,name='erp-upload'),
    path('erp/template/',views.erp_template,name='erp-template'),
    path('erp/<uuid:pk>/export/',views.erp_export,name='erp-export'),
    path('erp/<uuid:pk>/',views.erp_detail,name='erp-detail'),
    path('erp/rows/<int:pk>/review/',views.erp_review,name='erp-review'),
    path('receipts/',views.receipt_list,name='receipt-list'),
    path('receipts/<uuid:pk>/cancel/',views.receipt_cancel,name='receipt-cancel'),
    path('receipts/bulk/',views.receipt_bulk,name='receipt-bulk'),
    path('receipts/new/',views.receipt_create,name='receipt-create'),
    path('stock/<int:pk>/arrival/',views.arrival_confirm,name='arrival-confirm'),
]
