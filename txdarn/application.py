from txdarn import resources as R
from txdarn import protocol as P
from txdarn import compat

import klein


class TxDarn(object):

    app = klein.Klein()

    def __init__(self, factory, config):
        self._greeting = R.Greeting()

        self._iframe = R.IFrameResource(
            sockJSURL=config['sockjs-url'])

        self._info = R.InfoResource(
            websocketsEnabled=config['websockets'])

        self._sockJSFactory = P.SockJSProtocolFactory(factory)

        self._sessions = P.SessionHouse()

        self._xhrResource = R.XHRResource(self._sockJSFactory,
                                          self._sessions,
                                          timeout=config['timeout'])
        self._xhrSendResource = R.XHRSendResource(self._sessions)

    @app.route('/', strict_slashes=False)
    def greeting(self, request):
        return self._greeting

    @app.route('/iframe.html')
    @app.route('/iframe<ignore>.html')
    def iframe(self, request, ignore=None):
        return self._iframe

    @app.route('/info')
    def info(self, request):
        return self._info

    with app.subroute('/<serverID>/<sessionID>') as sessionApp:
        @sessionApp.route('/xhr')
        def xhr(self, request, serverID, sessionID):
            request.postpath = [compat.networkString(serverID),
                                compat.networkString(sessionID),
                                b'xhr']
            return self._xhrResource

        @sessionApp.route('/xhr_send')
        def xhr_send(self, request, serverID, sessionID):
            request.postpath = [compat.networkString(serverID),
                                compat.networkString(sessionID),
                                b'xhr_send']
            return self._xhrSendResource
