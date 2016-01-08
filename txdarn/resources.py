import random
import hashlib
import functools
import pkgutil
import time
from collections import namedtuple
from wsgiref.handlers import format_date_time

import eliot
from twisted.web import resource, template, http, server

import six

from zope.interface import Interface, implementer

from . import encoding, compat, protocol


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
                methods = tuple(resource.allowedMethods)
            except AttributeError:
                raise ValueError('Resource {!r} must have an allowedMethods'
                                 ' attribute when used with an INFERRED'
                                 ' AccessControlPolicy'.format(self))
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

        required = set(allowedMethods)
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


DEFAULT_CACHEABLE_POLICY = CachePolicy(
    cacheDirectives=(PUBLIC,
                     MAX_AGE(ONE_YEAR)),
    expiresOffset=ONE_YEAR)

DEFAULT_UNCACHEABLE_POLICY = CachePolicy(
    cacheDirectives=(NO_STORE,
                     NO_CACHE,
                     MUST_REVALIDATE,
                     MAX_AGE(0)),
    expiresOffset=None)


DEFAULT_ACCESS_CONTROL_POLICY = AccessControlPolicy(
    methods=INFERRED, maxAge=2000000)


class _OptionsMixin(resource.Resource):
    allowedMethods = ()

    def setAllow(self, request):
        request.setHeader(b'Allow', httpMultiValue(self.allowedMethods))
        return b''


class Greeting(resource.Resource):
    isLeaf = True
    allowedMethods = (b'GET',)

    @encoding.contentType(b'text/plain')
    def render_GET(self, request):
        return b'Welcome to SockJS!\n'


class IFrameElement(template.Element):
    loader = template.XMLString(pkgutil.get_data('txdarn',
                                                 'content/iframe.xml'))

    def __init__(self, sockJSURL):
        self.sockJSURL = sockJSURL

    @template.renderer
    def sockjsLocation(self, request, tag):
        tag.attributes[b'src'] = self.sockJSURL
        return tag(b'')

    # we have to manually insert these two attributes because
    # twisted.template (predictably) does not maintain attribute
    # order.  unfortunately, the official sockjs-protocol test does a
    # simple regex match against this page and so expects these to be
    # a specific order.  tag.attributes is an OrderedDict, so exploit
    # that here to enforce attribute ordering.
    @template.renderer
    def xUACompatible(self, request, tag):
        tag.attributes[b'http-equiv'] = b'X-UA-Compatible'
        tag.attributes[b'content'] = b'IE=edge'
        return tag()

    @template.renderer
    def contentType(self, request, tag):
        tag.attributes[b'http-equiv'] = b'Content-Type'
        tag.attributes[b'content'] = b'text/html; charset=UTF-8'
        return tag()


class IFrameResource(HeaderPolicyApplyingResource):
    isLeaf = True
    allowedMethods = (b'GET',)

    policies = ImmutableDict(
        {b'GET': (DEFAULT_CACHEABLE_POLICY,
                  DEFAULT_ACCESS_CONTROL_POLICY)})

    iframe = None
    etag = None

    _doctype = b'<!DOCTYPE html>'

    def __init__(self,
                 sockJSURL,
                 policies=None,
                 _render=functools.partial(template.flattenString,
                                           request=None)):
        HeaderPolicyApplyingResource.__init__(self, self.policies)

        self.element = IFrameElement(sockJSURL)

        renderingDeferred = _render(root=self.element)

        def _cbSetTemplate(iframe):
            self.iframe = b'\n'.join([self._doctype, iframe])

        renderingDeferred.addCallback(_cbSetTemplate)
        renderingDeferred.addErrback(eliot.writeFailure)

        if not self.iframe:
            raise RuntimeError("Could not render iframe!")

        hashed = hashlib.sha256(self.iframe).hexdigest()
        self.etag = compat.networkString(hashed)

    @encoding.contentType(b'text/html')
    def render_GET(self, request):
        if request.setETag(self.etag) is http.CACHED:
            return b''
        request = self.applyPolicies(request)
        return self.iframe


class InfoResource(_OptionsMixin, HeaderPolicyApplyingResource):
    allowedMethods = (b'GET', b'OPTIONS')
    isLeaf = True

    policies = ImmutableDict(
        {b'GET': (DEFAULT_UNCACHEABLE_POLICY,
                  DEFAULT_ACCESS_CONTROL_POLICY._replace(
                      methods=(b'GET',))),
         b'OPTIONS': (DEFAULT_CACHEABLE_POLICY,
                      DEFAULT_ACCESS_CONTROL_POLICY)})

    entropyRange = (0, 1 << 32)

    def __init__(self,
                 websocketsEnabled=True,
                 cookiesNeeded=True,
                 allowedOrigins=('*:*',),
                 policies=None,
                 _render=compat.asJSON,
                 _random=random.SystemRandom().randrange):
        HeaderPolicyApplyingResource.__init__(self, policies)

        self.websocketsEnabled = websocketsEnabled
        self.cookiesNeeded = cookiesNeeded
        self.allowedOrigins = list(allowedOrigins)

        self._render = _render
        self._random = _random

    def calculateEntropy(self):
        return self._random(*self.entropyRange)

    def render_OPTIONS(self, request):
        self.setAllow(request)
        self.applyPolicies(request)
        return b''

    @encoding.contentType(b'application/json')
    def render_GET(self, request):
        self.applyPolicies(request)
        return self._render({'websocket': self.websocketsEnabled,
                             'cookie_needed': self.cookiesNeeded,
                             'origins': self.allowedOrigins,
                             'entropy': self.calculateEntropy()})


class XHRResource(_OptionsMixin, HeaderPolicyApplyingResource):
    """Read side of the XHR polling transport."""
    allowedMethods = (b'POST', b'OPTIONS')
    isLeaf = True

    policies = ImmutableDict(
        {b'POST': (DEFAULT_UNCACHEABLE_POLICY,
                   DEFAULT_ACCESS_CONTROL_POLICY._replace(
                       methods=(b'POST',))),
         b'OPTIONS': (DEFAULT_CACHEABLE_POLICY,
                      DEFAULT_ACCESS_CONTROL_POLICY)})

    def __init__(self, sessions, policies=None):
        HeaderPolicyApplyingResource.__init__(self, policies)
        self.sessions = sessions

    def render_OPTIONS(self, request):
        self.setAllow(request)
        self.applyPolicies(request)
        return b''

    @encoding.contentType(b'application/json')
    def render_POST(self, request):
        if not (request.postpath[-1] == b'xhr',
                self.sessions.attachToSession(request)):
            request.setResponseCode(404)
            return b''
        return server.NOT_DONE_YET


class XHRSendResource(_OptionsMixin, HeaderPolicyApplyingResource):
    """Write side of the XHR polling transport."""
    allowedMethods = (b'POST', b'OPTIONS')
    isLeaf = True

    policies = ImmutableDict(
        {b'POST': (DEFAULT_UNCACHEABLE_POLICY,
                   DEFAULT_ACCESS_CONTROL_POLICY._replace(
                       methods=(b'POST',))),
         b'OPTIONS': (DEFAULT_CACHEABLE_POLICY,
                      DEFAULT_ACCESS_CONTROL_POLICY)})

    def __init__(self, sessions, policies=None):
        HeaderPolicyApplyingResource.__init__(self, policies)
        self.sessions = sessions

    def render_OPTIONS(self, request):
        self.setAllow(request)
        self.applyPolicies(request)
        return b''

    @encoding.contentType(b'application/json')
    def render_POST(self, request):
        try:
            if not (request.postpath[-1] == b'xhr_send' and
                    self.sessions.writeToSession(request)):
                request.setResponseCode(404)
            else:
                request.setResponseCode(204)
        except protocol.InvalidData as invalidException:
            request.setResponseCode(500)
            return invalidException.reason
        return b''
