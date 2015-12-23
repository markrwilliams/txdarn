from txdarn import resources as R

import klein


class TxDarn(object):

    app = klein.Klein()

    def __init__(self, config):
        self.config = config

        self._greeting = R.Greeting()
        self._iframe = R.IFrameResource(
            sockJSURL=self.config['sockjs-url'])
        self._info = R.InfoResource(
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
