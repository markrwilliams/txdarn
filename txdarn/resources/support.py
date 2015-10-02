import datetime
import functools
import pkgutil
from wsgiref.handlers import format_date_time

from twisted.web import resource, template
import eliot

from .. import encoding, compat


class SlashIgnoringResource(resource.Resource):

    def getChild(self, name, request):
        if not name:
            name = request.prepath[-1]
        return resource.Resource.getChildWithDefault(self, name, request)


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


class IFrameResource(resource.Resource):
    isLeaf = True
    iframe = None
    doctype = b'<!DOCTYPE html>'

    def __init__(self, sockJSURL,
                 _render=functools.partial(template.flattenString,
                                           request=None)):
        resource.Resource.__init__(self)
        self.element = IFrameElement(sockJSURL)

        renderingDeferred = _render(root=self.element)

        def _cbSetTemplate(iframe):
            self.iframe = b'\n'.join([self.doctype, iframe])

        renderingDeferred.addCallback(_cbSetTemplate)
        renderingDeferred.addErrback(eliot.writeFailure)

        if not self.iframe:
            raise RuntimeError("Could not render iframe!")

    @encoding.contentType(b'text/html')
    def render_GET(self, request):
        return self.iframe


class InfoResource(resource.Resource):
    isLeaf = True
    accessControlMaxAge = 2000000

    def __init__(self,
                 maximumAge=31536000,
                 _render=compat.asJSON):
        self.maximumAge = maximumAge
        self._render = _render

    def optionsForRequest(self, request,
                          datetimeNow=datetime.datetime.now):
        httpNow = format_date_time(datetimeNow())

        request.setHeader(b'Cache-Control',
                          compat.networkString(
                              "max-age=%d public" % self.maximumAge))

        request.setHeader(b'Expires',
                          compat.networkString(httpNow))

        request.setHeader(b'access-control-max-age',
                          compat.intToBytes(self.accessControlMaxAge))

        request.setHeader(b'Access-Control-Allow-Methods')

    @encoding.contentType(b'application/json')
    def render_GET(self, request):
        self._render({})
