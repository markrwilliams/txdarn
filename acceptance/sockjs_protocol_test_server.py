from twisted.internet import reactor
from twisted.web.resource import Resource
from twisted.web.server import Site, NOT_DONE_YET
from twisted.internet import endpoints
from twisted.internet.protocol import Factory, Protocol

from txdarn.resources import TxDarn


class EchoProtocol(Protocol):

    def dataReceived(self, data):
        self.transport.write(data)


class CloseProtocol(Protocol):

    def connectionMade(self):
        self.transport.loseConnection()


class AmplifyProtocol(Protocol):

    def _parseNumber(self, length):
        length = int(length)
        return length if 0 < length < 19 else 1

    def dataReceived(self, data):
        response = ["x" * 2 ** self._parseNumber(datum) for datum in data]
        self.transport.write(response)

options = {
    'sockJSURL': 'https://cdnjs.cloudflare.com/ajax'
    '/libs/sockjs-client/1.0.3/sockjs.js',
    'websocketsEnabled': True,
    'timeout': 5.0,
    'maximumBytes': 4096}

darnit = TxDarn(Factory.forProtocol(EchoProtocol), **options)
darnitClose = TxDarn(Factory.forProtocol(CloseProtocol), **options)

noWS = options.copy()
noWS['websocketsEnabled'] = False
darnitNoWS = TxDarn(Factory.forProtocol(EchoProtocol), **noWS)

darnitAmplify = TxDarn(Factory.forProtocol(AmplifyProtocol), **options)


class StreamingResource(Resource):

    def render_GET(self, request):
        request.setHeader(b'Content-Type', b'text/plain')
        request.setHeader(b'Access-Control-Allow-Origin', b'*')
        request.write(b'a' * 2048 + b'\n')

        def done():
            request.write(b'b\n')
            request.finish()

        reactor.callLater(0.25, done)
        return NOT_DONE_YET


testServer = Resource()
testServer.putChild(b'echo', darnit)
testServer.putChild(b'close', darnitClose)
testServer.putChild(b'disabled_websocket_echo', darnitNoWS)
testServer.putChild(b'amplify', darnitAmplify)
testServer.putChild(b'streaming.txt', StreamingResource())


def runWithoutLog():
    site = Site(testServer, logPath=b'/tmp/log')
    endpoints.serverFromString(reactor, 'tcp:8081').listen(site)
    reactor.run()


def runWithLog():
    import sys
    from twisted.python import log
    log.startLogging(sys.stdout)
    runWithoutLog()

runWithLog()
