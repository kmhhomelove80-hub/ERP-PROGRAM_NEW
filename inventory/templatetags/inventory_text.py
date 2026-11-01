from django import template

register = template.Library()


@register.filter
def single_line(value):
    """Display multiline source specifications on one row without changing data."""
    return ' '.join(str(value).split()) if value is not None else ''
