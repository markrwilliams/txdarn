from twisted.web.server import NOT_DONE_YET
from txdarn import resources as R
from txdarn import protocol as P

import klein


class TxDarn(object):

    app = klein.Klein()

    def __init__(self, factory, config):
        self.config = config

        self._greeting = R.Greeting()
        self._iframe = R.IFrameResource(
            sockJSURL=self.config['sockjs-url'])
        self._info = R.InfoResource(
            websocketsEnabled=self.config['websockets'])

        self._sockJSFactory = P.SockJSProtocolFactory(factory)

        self._xhrFactory = P.XHRSessionFactory(self._sockJSFactory)
        self._sessions = P.SessionHouse(self._xhrFactory,
                                        self.config['timeout'])

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
            if not self._sessions.attachToSession(serverID,
                                                  sessionID,
                                                  request):
                request.setResponseCode(404)
                return b''
            return request.notifyFinish()

        @sessionApp.route('/xhr_send')
        def xhr_send(self, request, serverID, sessionID):
            if self._sessions.writeToSession(serverID,
                                             sessionID,
                                             request.content.read()):
                request.setResponseCode(204)
            else:
                request.setResponseCode(404)
            return ''
