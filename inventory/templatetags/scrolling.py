from django import template
register=template.Library()
@register.simple_tag(takes_context=True)
def next_page_url(context,page):
 request=context.get('request')
 if not page or not page.has_next():return ''
 params=request.GET.copy();params['page']=page.next_page_number();params.pop('partial',None)
 return '?'+params.urlencode()
