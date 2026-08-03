"""Minimal stand-in for the `requests` package.

The gitcensor example imports it at module scope. Bundling must leave such a
third-party import alone and let it resolve normally at runtime, so the test
only needs the import to succeed.
"""


class RequestException(Exception):
    pass


class Response:
    status_code = 200
    headers = {}

    def json(self):
        return []

    def raise_for_status(self):
        return None


class Session:
    headers = {}

    def get(self, *args, **kwargs):
        return Response()

    def request(self, *args, **kwargs):
        return Response()


def get(*args, **kwargs):
    return Response()
