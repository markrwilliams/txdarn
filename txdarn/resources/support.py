import random
import hashlib
import datetime
import functools
import pkgutil
import eliot
from twisted.web import resource, template, http

from .. import encoding, compat
from . import headers


DEFAULT_CACHEABLE_POLICY = headers.CachePolicy(
    cacheDirectives=(headers.PUBLIC,
                     headers.MAX_AGE(headers.ONE_YEAR)),
    expiresOffset=headers.ONE_YEAR)

DEFAULT_UNCACHEABLE_POLICY = headers.CachePolicy(
    cacheDirectives=(headers.NO_STORE,
                     headers.NO_CACHE,
                     headers.MUST_REVALIDATE,
                     headers.MAX_AGE(0)),
    expiresOffset=None)


class Greeting(resource.Resource):
    isLeaf = True

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


class IFrameResource(headers.HeaderPolicyApplyingResource):
    isLeaf = True

    iframe = None
    etag = None
    doctype = b'<!DOCTYPE html>'

    def __init__(self,
                 sockJSURL,
                 policies=(DEFAULT_CACHEABLE_POLICY,
                           headers.AccessControlPolicy(methods=(b'GET',
                                                                b'OPTIONS'),
                                                       maxAge=2000000)),
                 _render=functools.partial(template.flattenString,
                                           request=None)):
        headers.HeaderPolicyApplyingResource.__init__(self, policies)
        self.element = IFrameElement(sockJSURL)

        renderingDeferred = _render(root=self.element)

        def _cbSetTemplate(iframe):
            self.iframe = b'\n'.join([self.doctype, iframe])

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


class OptionsSubResource(headers.HeaderPolicyApplyingResource):
    isLeaf = True

    def __init__(self, policies):
        headers.HeaderPolicyApplyingResource.__init__(self, policies)

    def render_OPTIONS(self, request):
        self.applyPolicies(request)
        return b''


class InfoResource(headers.HeaderPolicyApplyingResource):
    isLeaf = True
    entropyRange = (0, 1 << 32)

    def __init__(self,
                 websocketsEnabled=True,
                 cookiesNeeded=True,
                 allowedOrigins=('*:*',),
                 policies=(DEFAULT_UNCACHEABLE_POLICY,
                           headers.AccessControlPolicy(methods=(b'GET',),
                                                       maxAge=2000000)),
                 infoPolicies=(
                     DEFAULT_CACHEABLE_POLICY,
                     headers.AccessControlPolicy(methods=(b'GET',
                                                          b'OPTIONS'),
                                                 maxAge=2000000)),
                 _render=compat.asJSON,
                 _random=random.SystemRandom().randrange):
        headers.HeaderPolicyApplyingResource.__init__(self, policies)

        self.websocketsEnabled = websocketsEnabled
        self.cookiesNeeded = cookiesNeeded
        self.allowedOrigins = list(allowedOrigins)

        self._render = _render
        self._random = _random

        self.optionsResource = OptionsSubResource(infoPolicies)
        self.render_OPTIONS = self.optionsResource.render_OPTIONS

    def calculateEntropy(self):
        return self._random(*self.entropyRange)

    @encoding.contentType(b'application/json')
    def render_GET(self, request):
        self.applyPolicies(request)
        return self._render({'websocket': self.websocketsEnabled,
                             'cookie_needed': self.cookiesNeeded,
                             'origins': self.allowedOrigins,
                             'entropy': self.calculateEntropy()})
