from django import template

register = template.Library()


@register.filter
def single_line(value):
    """Display multiline source specifications on one row without changing data."""
    return ' '.join(str(value).split()) if value is not None else ''

@register.filter
def stocktake_state(value):
    return {'open':'진행 중','applied':'반영 완료','reversed':'되돌리기 완료','cancelled':'회차 취소'}.get(value,value)
