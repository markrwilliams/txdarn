import time
from collections import namedtuple
from wsgiref.handlers import format_date_time

import six

from twisted.web import resource
from zope.interface import Interface, implementer
from .. import compat


class ImmutableDict(compat.Mapping):

    def __init__(self, *args, **kwargs):
        self._dict = dict(*args, **kwargs)

    def __getitem__(self, key):
        return self._dict[key]

    def __iter__(self):
        return iter(self._dict)

    def __len__(self):
        return len(self._dict)

    def __repr__(self):
        className = self.__class__.__name__
        return '{}({!r})'.format(className, self._dict)

    if not six.PY3:
        # a bummer of a hack; subtraction between a set and a KeysView
        # is not commutative in python 2.  this fails with a TypeError:
        # set('a') - KeysView({'a': 1})
        # while this does not:
        # KeysView('a') - {'a': 1}
        def viewkeys(self):
            return self._dict.viewkeys()


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


# use me to tell AccessControlPolicy to ask the resource about what
# methods it supports
INFERRED = 'INFERRED'


class IHeaderPolicy(Interface):
    '''
    A header policy

    I encapsulate Response header additions that depend on the
    incoming request.

    Use me with L{HeaderPolicyApplyingResource}.
    '''

    def forResource(resource):
        '''
        Return a Policy object configured for the the provided resource.
        Implement this if your policy needs to inspect the resource
        whose responses the policy will modify.

        @param resource: a C{twisted.web.resource.Resource} instance
        @type resource: L{twisted.web.resource.Resource}
        '''

    def apply(request):
        '''
        Apply this header policy to the given request.

        @param request: a C{twisted.web.server.Request} instance.  It
        should not be finished.
        @type resource: L{twisted.web.server.Request}
        '''


@implementer(IHeaderPolicy)
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

    def forResource(self, resource):
        return self

    def apply(self, request, now=time.time):
        if self.expiresOffset is not None:
            expires = compat.networkString(
                format_date_time(now() + self.expiresOffset))

            request.setHeader(b'expires', expires)
        request.setHeader(b'cache-control', self.cacheControlFormatted)

        return request


def allowOrigin(policy, request, origin):
    if origin is None:
        return b'*'
    return origin


def allowCredentials(policy, request, origin):
    if origin not in (b'*', None):
        return b'true'


@implementer(IHeaderPolicy)
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

    def forResource(self, resource):
        if self.methods is INFERRED:
            try:
                methods = tuple(compat.networkString(method)
                                for method in resource.allowedMethods)
            except AttributeError:
                raise ValueError('Resource {!r} must have an allowedMethods'
                                 ' attribute when used with an INFERRED'
                                 ' AccessControlPolicy')
            else:
                return self._replace(methods=methods)
        else:
            return self

    def apply(self, request):
        origin = request.getHeader(b'origin')

        allowedOrigin = self.allowOrigin(self, request, origin)
        credentialsAllowed = self.allowCredentials(
            self, request, allowedOrigin)

        methods = httpMultiValue(self.methods)
        maxAge = compat.networkString(str(self.maxAge))

        request.setHeader(b'access-control-allow-methods', methods)
        request.setHeader(b'access-control-max-age', maxAge)

        request.setHeader(b'access-control-allow-origin', allowedOrigin)
        if credentialsAllowed:
            request.setHeader(b'access-control-allow-credentials',
                              credentialsAllowed)

        return request


class HeaderPolicyApplyingResource(resource.Resource):
    policies = None

    def __init__(self, policies=None):
        if policies is None:
            policies = self.policies

        if not isinstance(policies, compat.Mapping):
            raise ValueError("policies must be a mapping of bytes"
                             " method names to sequence of policies.")

        allowedMethods = getattr(self, 'allowedMethods', None)
        if not allowedMethods:
            raise ValueError("instance must have allowedMethods")

        required = set(compat.networkString(method)
                       for method in allowedMethods)
        available = six.viewkeys(policies)
        missing = required - available

        if missing:
            missing = {compat.stringFromNetwork(method)
                       for method in missing}
            raise ValueError("missing methods: {}".format(missing))

        # adapt any policies we have to our resource
        self._actingPolicies = {method: tuple(p.forResource(self)
                                              for p in methodPolicies)
                                for method, methodPolicies in policies.items()}

    def applyPolicies(self, request):
        '''
        Apply relevant header policies to request.  Call me where
        appropriate in your render_* methods.
        '''
        for policy in self._actingPolicies[request.method]:
            request = policy.apply(request)
        return request
