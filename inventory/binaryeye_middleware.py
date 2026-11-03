"""Normalize Binary Eye browser forwarding into the existing exact-code search."""
from urllib.parse import urlencode
from django.http import HttpResponseRedirect


class BinaryEyeSearchMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.method == 'GET' and request.path == '/' and request.GET.get('scan') == '1'
                and not request.GET.get('q', '').strip()):
            code = request.GET.get('content', '').strip()
            if 0 < len(code) <= 100 and not any(ord(c) < 32 or ord(c) == 127 for c in code):
                return HttpResponseRedirect('/?' + urlencode({'scan':'1', 'q':code}))
        return self.get_response(request)
