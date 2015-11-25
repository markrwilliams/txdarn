import time
from collections import namedtuple
from wsgiref.handlers import format_date_time

from twisted.web import resource
from .. import compat


PUBLIC = b'public'
NO_CACHE = b'no-cache'          # TODO: this should be a function,
                                # that takes fields
NO_STORE = b'no-store'
MUST_REVALIDATE = b'must-revalidate'


def MAX_AGE(age):
    return compat.networkString('max-age={:d}'.format(age))


ONE_YEAR = 3153600


def httpMultiValue(values):
    return b', '.join(values)


class CachePolicy(namedtuple('CachePolicy',
                             ('cacheDirectives',
                              'expiresOffset',
                              'cacheControlFormatted'))):

    def __new__(cls, cacheDirectives, expiresOffset):
        cacheControlFormatted = httpMultiValue(cacheDirectives)
        return super(CachePolicy, cls).__new__(cls,
                                               cacheDirectives,
                                               expiresOffset,
                                               cacheControlFormatted)

    def apply(self, request, now=time.time):
        if self.expiresOffset is not None:
            expires = compat.networkString(
                format_date_time(now() + self.expiresOffset))

            request.setHeader(b'expires', expires)
        request.setHeader(b'cache-control', self.cacheControlFormatted)

        return request


def allowOrigin(policy, request, origin):
    if origin in (b'null', None):
        return b'*'
    return origin


def allowCredentials(policy, request, origin):
    if origin not in (b'*', None):
        return b'true'


class AccessControlPolicy(namedtuple('AccessControlPolicy',
                                     ['methods',
                                      'maxAge',
                                      'allowOrigin',
                                      'allowCredentials'])):

    def __new__(cls, methods, maxAge,
                allowOrigin=allowOrigin,
                allowCredentials=allowCredentials):
        return super(AccessControlPolicy, cls).__new__(
            cls, methods, maxAge, allowOrigin, allowCredentials)

    def apply(self, request):
        origin = request.getHeader(b'origin')

        allowedOrigin = self.allowOrigin(self, request, origin)
        credentialsAllowed = self.allowCredentials(
            self, request, allowedOrigin)

        methods = httpMultiValue(self.methods)
        maxAge = compat.networkString(str(self.maxAge))

        request.setHeader(b'access-control-allow-methods', methods)
        request.setHeader(b'access-control-max-age', maxAge)

        request.setHeader(b'access-control-allowed-origin', allowedOrigin)
        if credentialsAllowed:
            request.setHeader(b'access-control-allow-credentials',
                              credentialsAllowed)

        return request


class HeaderPolicyApplyingResource(resource.Resource):

    def __init__(self, policies):
        self.policies = policies

    def applyPolicies(self, request):
        for policy in self.policies:
            request = policy.apply(request)
        return request
