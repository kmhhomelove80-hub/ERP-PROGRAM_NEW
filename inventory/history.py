def event_summary(event):
    d = event.detail
    if event.action=='inbound_work_edit':
        return ' / '.join(f"{r.get('line')}행 · {r.get('location') or '미입력'} · "+('배치 완료' if r.get('placed') else '배치 미완료') for r in d.get('after',[]))
    if event.action in ('inbound_work_create','inbound_work_receive'):return f"{d.get('count',0)}행"+(f" · 총 {d['quantity']}개" if 'quantity' in d else '')
    if event.action in ('inbound_work_hide','inbound_work_restore'):return '저장 목록 표시만 변경 · 실제 재고 유지'
    if event.action=='company_print_queue':return f"{d.get('copies',1)}부 · 인쇄 대기"
    if event.action=='company_print_result':return {'submitted':'인쇄 대기열 전달','unknown':'현장 결과 확인 필요'}.get(d.get('state'),'확인 필요')
    if event.action=='company_print_cancel':return '대기 상태의 인쇄 요청 취소'
    if event.action=='company_printer_settings':return '출력 허용' if d.get('enabled') else '출력 중지'

    if event.action=='stocktake_apply':return f"실사·조정 {d.get('rows',0)}행 / 증감 {d.get('delta',0)} / 검증 백업 후 반영"
    if event.action=='stocktake_reverse':return f"실사·조정 {d.get('rows',0)}행 되돌리기"
    if event.action=='cleanup_keep':return f"{d.get('source_location','')} → {d.get('destination','')} · 기존 유지"
    if event.action=='cleanup_keep_cancel':return '기존 유지 판정 취소'
    if event.action == 'allocation_reassign':
        before = d.get('before', {})
        source = before.get('details', {}).get('location', '')
        after = ' / '.join(f"{r.get('details',{}).get('location','')} {r.get('quantity',0)}개" for r in d.get('after', []))
        return f"{source} {before.get('quantity',0)}개 → {after} · {d.get('reason','')}"
    if event.action.startswith('substitution_'):
        return f"{d.get('requested_code','')} ← {d.get('actual_code','')}" + (f" · {d['quantity']}개" if 'quantity' in d else '') + f" · {d.get('note',d.get('reason',''))}"
    if event.action in ['movement','movement_requested']:
        source = d.get('source_location',d.get('location',''))
        destination = d.get('destination','')
        state = {'completed':'완료','pending':'대기','rejected':'거부'}.get(d.get('state'),'요청 등록')
        return f"{source} → {destination} · {state}" + (f" · {d['reason']}" if d.get('reason') else '')
    if event.action == 'initial_import':
        return f"초기 재고 {d.get('stock_rows',0)}행 / 예약 {d.get('allocation_rows',0)}행"
    if event.action=='erp_snapshot': return f"{d.get('label','')} · {d.get('rows',0)}행 대조"
    if event.action=='reconciliation_review': return f"{d.get('warehouse','')} · {d.get('reason','')} · {d.get('action_note','')}"
    if event.action=='receipt_cancel': return f"{d.get('location','')} · {d.get('reason','')}"
    if event.action == 'receipt_batch': return f"일괄 입고 {d.get('count',0)}행 · 총 {d.get('quantity',0)}개"
    if event.action == 'receipt':
        return f"{d.get('location','')} · " + ('입고 예정 등록' if d.get('status')=='expected' else '입고 등록')
    if event.action == 'arrival':
        return f"{d.get('source_location','')} → {d.get('destination','')} · 총량 유지, 예약 이전"
    if event.action == 'picking_confirm':
        return f"{d.get('project','')} · 부족 품목 {len(d.get('shortages',[]))}종"
    return str(d.get('project',''))
