from txdarn.resources import support as S

import klein


class TxDarn(object):

    app = klein.Klein()

    def __init__(self, config):
        self.config = config

        self._greeting = S.Greeting()
        self._iframe = S.IFrameResource(
            sockJSURL=self.config['sockjs-url'])
        self._info = S.InfoResource(
            websocketsEnabled=self.config['websockets'])

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
