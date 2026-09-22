from scripts import notifications


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
