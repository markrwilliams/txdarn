import pkgutil

from twisted.web import resource, template

from . import encoding


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

    def __init__(self, sockJSURL):
        resource.Resource.__init__(self)
        self.element = IFrameElement(sockJSURL)

    @encoding.contentType(b'text/html')
    def render_GET(self, request):
        return template.renderElement(request, self.element)
