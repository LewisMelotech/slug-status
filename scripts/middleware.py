from django.http import HttpResponse

from scripts import constants, notifications

UPLOAD_PATH_PREFIXES = ("/script/upload", "/api/scripts/")
BODY_METHODS = ("POST", "PUT", "PATCH")


class UploadSizeLimitMiddleware:
    """
    Reject oversized upload requests using the Content-Length header, before Django reads or spools the body.

    The field validators still run afterwards and give friendly errors for files that are over the individual
    JSON/PDF limits; this only stops requests that are far larger than any valid upload.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in BODY_METHODS and request.path.startswith(UPLOAD_PATH_PREFIXES):
            try:
                content_length = int(request.headers.get("Content-Length", ""))
            except ValueError:
                return HttpResponse("A Content-Length header is required.", status=411, content_type="text/plain")
            if content_length > constants.MAX_UPLOAD_REQUEST_BYTES:
                return HttpResponse("This upload is too large.", status=413, content_type="text/plain")
        return self.get_response(request)


class RememberRequest:
    """Lets an arrivals announcement say who is signed in, for the rest of this request.

    scripts.notifications reads this from thread-local state rather than being passed a
    request explicitly, because the code that actually creates a version — a model save,
    reached through a form, a serializer, an import or the shell — has no request of its
    own to be handed one. Forgetting it in a ``finally`` is what stops one request's
    identity leaking into whichever runs on this thread next.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        notifications.remember_request(request)
        try:
            return self.get_response(request)
        finally:
            notifications.forget_request()
