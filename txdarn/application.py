from txdarn.resources import support as S

import klein


class TxDarn(object):

    app = klein.Klein()

    def __init__(self, config):
        self.config = config

        self._greeting = S.Greeting()
        self._iframe = S.IFrameResource(self.config['sockjs-url'])

    @app.route('/', strict_slashes=False)
    def greeting(self, request):
        return self._greeting

    @app.route('/iframe.html')
    @app.route('/iframe<ignore>.html')
    def iframe(self, request, ignore=None):
        return self._iframe
