from twisted.python.constants import Values, ValueConstant
from twisted.internet import reactor
from txdarn.compat import asJSON, fromJSON
from automat import MethodicalMachine


def sockJSJSON(data):
    # no spaces
    return asJSON(data, separators=(',', ':'))


class DISCONNECT(Values):
    GO_AWAY = ValueConstant([3000, "Go away!"])


class HeartbeatClock(object):
    _machine = MethodicalMachine()
    pendingHeartbeat = None

    @staticmethod
    def graphviz(machine=_machine):  # pragma: no cover
        return machine.graphviz()

    def __init__(self, writeHeartbeat, period=25.0, clock=reactor):
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

    def __init__(self, heartbeater):
        self.heartbeater = heartbeater

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
        self.transport.write(b'a' + sockJSJSON(data))
        self.heartbeater.schedule()

    @_machine.input()
    def receive(self, data):
        '''Data has arrived!'''

    @_machine.output()
    def _received(self, data):
        return fromJSON(data)

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
