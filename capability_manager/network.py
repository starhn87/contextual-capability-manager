"""Network helper that never follows a request to another URL."""

import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def open_no_redirect(request, timeout):
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)
