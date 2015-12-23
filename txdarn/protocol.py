from automat import MethodicalMachine

import eliot

from twisted.python.constants import Values, ValueConstant
from twisted.internet import reactor
from twisted.protocols.policies import ProtocolWrapper, WrappingFactory

import six.moves

from txdarn.compat import asJSON, fromJSON


def sockJSJSON(data, cls=None):
    # no spaces
    return asJSON(data, separators=(',', ':'), cls=cls)


class DISCONNECT(Values):
    GO_AWAY = ValueConstant([3000, "Go away!"])
    STILL_OPEN = ValueConstant([2010, "Another connection still open"])


class HeartbeatClock(object):
    _machine = MethodicalMachine()
    writeHeartbeat = None
    pendingHeartbeat = None

    @staticmethod
    def graphviz(machine=_machine):  # pragma: no cover
        return machine.graphviz()

    def __init__(self, writeHeartbeat=None, period=25.0, clock=reactor):
        self.writeHeartbeat = writeHeartbeat
        self.period = period
        self.clock = clock

    @_machine.state(initial=True)
    def unscheduled(self):
        '''No heartbeat is scheduled.'''

    @_machine.state()
    def scheduled(self):
        '''A heartbeat is pending.'''

    @_machine.state()
    def stopped(self):
        '''The clock is permanently stopped.'''

    @_machine.input()
    def sent(self):
        '''A heartbeat has been sent.'''

    @_machine.input()
    def schedule(self):
        '''Schedule another heartbeat, cancel any pending'''

    @_machine.input()
    def stop(self):
        '''Stop this clock forever'''

    @_machine.output()
    def _reschedule(self):
        self.schedule()

    @_machine.output()
    def _resetTheClock(self):
        '''Drop our pending heartbeat because it's either been completed or
        canceled.

        '''
        self.pendingHeartbeat = None

    @_machine.output()
    def _stopTheClock(self):
        '''Cancel our pending heartbeat.'''
        self.pendingHeartbeat.cancel()

    def _sendHeartbeat(self):
        '''Complete our pending heartbeat.'''
        self.writeHeartbeat()
        self.sent()

    @_machine.output()
    def _startTheClock(self):
        '''Schedule our next heartbeat.'''
        self.pendingHeartbeat = self.clock.callLater(self.period,
                                                     self._sendHeartbeat)

    unscheduled.upon(schedule, enter=scheduled, outputs=[_startTheClock])
    unscheduled.upon(stop, enter=stopped, outputs=[])

    scheduled.upon(schedule, enter=unscheduled, outputs=[_stopTheClock,
                                                         _resetTheClock,
                                                         _reschedule])
    scheduled.upon(sent, enter=unscheduled, outputs=[_resetTheClock,
                                                     _reschedule])

    scheduled.upon(stop, enter=stopped, outputs=[_stopTheClock,
                                                 _resetTheClock])


class SockJSProtocolMachine(object):
    _machine = MethodicalMachine()
    transport = None

    @staticmethod
    def graphviz(machine=_machine):  # pragma: no cover
        return machine.graphviz()

    def __init__(self, heartbeater, jsonEncoder=None, jsonDecoder=None):
        self.heartbeater = heartbeater
        self.jsonEncoder = jsonEncoder
        self.jsonDecoder = jsonDecoder

    @classmethod
    def withHeartbeater(cls, heartbeater, jsonEncoder=None, jsonDecoder=None):
        """Connect a SockJSProtocolMachine to its heartbeater."""
        instance = cls(heartbeater, jsonEncoder, jsonDecoder)
        heartbeater.writeHeartbeat = instance.heartbeat
        return instance

    @_machine.state(initial=True)
    def notYetConnected(self):
        '''A connection has not yet been made.'''

    @_machine.state()
    def connected(self):
        '''We have a connection!'''

    @_machine.state()
    def disconnected(self):
        '''We have been disconnected.'''

    @_machine.input()
    def connect(self, transport):
        '''Establish a connection on the transport.'''

    @_machine.output()
    def _connectionEstablished(self, transport):
        '''Store a reference to our transport and write an open frame.'''
        self.transport = transport
        self.transport.write(b'o')
        self.heartbeater.schedule()

    @_machine.input()
    def write(self, data):
        '''We should write an array-like thing to the transport.'''

    @_machine.output()
    def _writeToTransport(self, data):
        '''Frame the array-like thing and write it.'''
        self.transport.write(b'a' + sockJSJSON(data, cls=self.jsonEncoder))
        self.heartbeater.schedule()

    @_machine.input()
    def receive(self, data):
        '''Data has arrived!'''

    @_machine.output()
    def _received(self, data):
        return fromJSON(data, cls=self.jsonDecoder)

    @_machine.input()
    def heartbeat(self):
        '''Time to send a heartbeat.'''

    @_machine.output()
    def _writeHeartbeatToTransport(self):
        '''Write a heartbeat frame'''
        self.transport.write(b'h')

    @_machine.input()
    def disconnect(self, reason=DISCONNECT.GO_AWAY):
        '''We're closing the connection because of reason.'''

    @_machine.output()
    def _writeCloseFrame(self, reason=DISCONNECT.GO_AWAY):
        '''Write a close frame with the given reason and schedule this
        connection close.

        '''
        self.heartbeater.stop()
        self.transport.write(b'c' + sockJSJSON(reason.value))
        self.transport.loseConnection()
        self.transport = None

    notYetConnected.upon(connect,
                         enter=connected,
                         outputs=[_connectionEstablished])
    notYetConnected.upon(disconnect,
                         enter=disconnected,
                         outputs=[])

    connected.upon(write,
                   enter=connected,
                   outputs=[_writeToTransport])
    connected.upon(receive,
                   enter=connected,
                   outputs=[_received],
                   collector=next)
    connected.upon(heartbeat,
                   enter=connected,
                   outputs=[_writeHeartbeatToTransport])
    connected.upon(disconnect,
                   enter=disconnected,
                   outputs=[_writeCloseFrame])


loggingRepr = six.moves.reprlib.Repr().repr


class SockJSProtocol(ProtocolWrapper):
    # TODO: losing the connection

    def __init__(self, factory, wrappedProtocol, sockJSMachine):
        ProtocolWrapper.__init__(self, factory, wrappedProtocol)
        self.sockJSMachine = sockJSMachine

    def connectionMade(self):
        action_type = '{}.dataReceived'.format(self.__class__.__name__)
        try:
            with eliot.start_action(action_type=action_type):
                self.sockJSMachine.connect(self.transport)
        except ValueError:
            pass
        else:
            self.wrappedProtocol.connectionMade()

    def dataReceived(self, data):
        action_type = '{}.dataReceived'.format(self.__class__.__name__)
        try:
            with eliot.start_action(action_type=action_type):
                eliot.Message.log(data=loggingRepr(data))

                decoded = self.sockJSMachine.receive(data)

        except Exception:
            pass
        else:
            self.wrappedProtocol.dataReceived(decoded)

    def write(self, data):
        try:
            action_type = '{}.write'.format(self.__class__.__name__)
            with eliot.start_action(action_type=action_type):
                eliot.Message.log(data=loggingRepr(data))

                self.sockJSMachine.write([data])

        except Exception:
            pass

    def writeSequence(self, data):
        try:
            action_type = '{}.writeSequence'.format(self.__class__.__name__)
            with eliot.start_action(action_type=action_type):
                eliot.Message.log(data=loggingRepr(data))

                self.sockJSMachine.write(data)

        except Exception:
            pass

    def loseConnection(self):
        try:
            action_type = '{}.loseConnection'.format(self.__class__.__name__)
            with eliot.start_action(action_type=action_type):

                self.sockJSMachine.disconnect()

        except Exception:
            pass


class SockJSProtocolFactory(WrappingFactory):
    """Factory that wraps another factory to provide the SockJS protocol.

    """

    protocol = SockJSProtocol

    def __init__(self, wrappedProtocol,
                 jsonEncoder=None, jsonDecoder=None,
                 heartbeatPeriod=25.0, clock=reactor):
        WrappingFactory.__init__(self, wrappedProtocol)

        heartbeater = HeartbeatClock(period=heartbeatPeriod, clock=clock)
        self.sockJSMachine = SockJSProtocolMachine.withHeartbeater(
            heartbeater, jsonEncoder, jsonDecoder)

    def buildProtocol(self, addr):
        return self.protocol(self, self.wrappedFactory.buildProtocol(addr),
                             self.sockJSMachine)
