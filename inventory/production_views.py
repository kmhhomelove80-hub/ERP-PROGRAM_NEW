import uuid
from pathlib import PurePath
from django.contrib import messages
from django.db import transaction
from django.core import signing
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from .access import warehouse_access, warehouse_write
from .models import ProductionPlanDraft, ProductionPlanSnapshot
from .production import MAX_UPLOAD, open_workbook, settings_for, extract
from .production_mapping import KEYS, LABELS, MappingError

@warehouse_access
@require_http_methods(['GET'])
def production_plan(request):
    snapshot=ProductionPlanSnapshot.objects.filter(pk=1).first()
    return render(request,'inventory/production_plan.html',{'snapshot':snapshot,'headers':['원본 행',*LABELS[:9]],'plan_data':snapshot.rows if snapshot else [],'part_headers':LABELS[9:]})

@warehouse_write
@require_http_methods(['GET','POST'])
def production_settings(request):
    draft,_=ProductionPlanDraft.objects.get_or_create(user=request.user)
    error=''; fields=[]; sheets=[]; config=dict(draft.config); choices=[]; configuration_token=''
    if request.method=='POST' and request.POST.get('action')=='upload':
        upload=request.FILES.get('workbook')
        if not upload or not upload.name.lower().endswith(('.xlsx','.xlsm')):
            error='XLSX 또는 XLSM 파일을 선택해 주세요.'
        elif upload.size>MAX_UPLOAD:
            error='10MB 이하의 파일을 선택해 주세요.'
        else:
            content=upload.read(MAX_UPLOAD+1)
            try:
                wb=open_workbook(content); wb.close()
                draft.workbook=content; draft.filename=PurePath(upload.name).name[:255];draft.revision=uuid.uuid4()
                draft.save(update_fields=['workbook','filename','revision'])
                return redirect('production-settings')
            except Exception:
                error='엑셀 파일을 읽을 수 없습니다. 파일 형식과 크기를 확인해 주세요.'
    if draft.workbook:
        wb=None
        try:
            wb=open_workbook(bytes(draft.workbook));sheets=wb.sheetnames
            if request.method=='POST' and request.POST.get('action') in ('inspect','apply'):
                config.update({k:request.POST.get(k,'') for k in ('sheet','first','last','start')})
            # Never silently switch away from a previously selected missing sheet.
            selected_sheet=config.get('sheet')
            if selected_sheet and selected_sheet not in sheets:
                if request.method=='POST' and request.POST.get('action')=='apply':
                    raise ValueError('선택한 시트가 없습니다. 시트를 다시 선택해 주세요.')
                config['sheet']=sheets[0]
                error=error or '저장된 시트가 없습니다. 시트를 선택하고 열 목록을 확인해 주세요.'
            ws,headers,base=settings_for(wb,config);config.update(base)
            configuration_token=signing.dumps({'revision':str(draft.revision),**base},salt='production-config')
            choices=[{'column':col,'label':f'{col} · {label or "(헤더 없음)"}','preview':' / '.join(str(ws[f'{col}{r}'].value or '')[:65] for r in range(base['start'],min(base['start']+2,ws.max_row+1)))} for col,label in headers.items()]
            suggested={}
            for key in KEYS:
                # Reuse selectors, never saved physical positions.
                try: suggested[key]=resolve_one(headers,key,config.get('selectors',{}))
                except MappingError: pass
            chosen = {key:request.POST.get('col_'+key,'') for key in KEYS} if request.method=='POST' and request.POST.get('action')=='apply' else suggested
            fields=[{'key':key,'label':label,'selected':chosen.get(key,''),'unresolved':not chosen.get(key)} for key,label in zip(KEYS,LABELS)]
            if request.method=='POST' and request.POST.get('action')=='apply':
                if request.POST.get('revision')!=str(draft.revision):
                    raise ValueError('다른 창에서 파일이 변경되었습니다. 새로고침 후 다시 확인해 주세요.')
                try:
                    shown=signing.loads(request.POST.get('configuration_token',''),salt='production-config',max_age=86400)
                except signing.BadSignature:
                    raise ValueError('열 목록을 다시 확인한 뒤 적용해 주세요.')
                if shown != {'revision':str(draft.revision),**base}:
                    raise ValueError('시트 또는 행 범위가 바뀌었습니다. 열 목록을 먼저 확인해 주세요.')
                rows,confirmed=extract(wb,config,chosen)
                with transaction.atomic():
                    locked=ProductionPlanDraft.objects.select_for_update().get(pk=draft.pk)
                    if locked.revision!=draft.revision: raise ValueError('파일이 변경되었습니다. 다시 확인해 주세요.')
                    locked.config=confirmed;locked.save(update_fields=['config'])
                    ProductionPlanSnapshot.objects.update_or_create(pk=1,defaults={'rows':rows,'filename':draft.filename,'config':confirmed,'uploaded_by':request.user})
                messages.success(request,f'생산계획 {len(rows):,}건을 적용했습니다.')
                return redirect('production-plan')
        except (ValueError,TypeError) as exc:
            error=str(exc)
        except Exception:
            error='엑셀을 읽을 수 없습니다. 파일과 헤더 범위를 확인해 주세요.'
        finally:
            if wb: wb.close()
    return render(request,'inventory/production_settings.html',{'draft':draft,'config':config,'sheets':sheets,'choices':choices,'fields':fields,'error':error,'configuration_token':configuration_token})

def resolve_one(headers,key,selectors):
    # Resolve independently so one missing header does not discard other matches.
    from .production_mapping import normalize, ALIASES
    label=LABELS[KEYS.index(key)]; saved=selectors.get(key)
    names=(saved,) if saved else (label,*ALIASES.get(key,()))
    normalized={normalize(n) for n in names if n}
    found=[col for col,text in headers.items() if normalize(text) in normalized]
    found=found or [col for col,text in headers.items() if normalize(text.split(' / ')[-1]) in normalized]
    if len(found)!=1: raise MappingError(label)
    return found[0]
