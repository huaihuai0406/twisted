"""Basic protocols, such as line-oriented, netstring, and 32-bit-int prefixed strings.
"""


import string
import re
import struct

from twisted.protocols import protocol


LENGTH, DATA, COMMA = range(3)
NUMBER = re.compile('(\d*)(:?)')

class NetstringParseError(ValueError):
    '''The incoming data is not in valid Netstring format'''
    pass

class NetstringReceiver(protocol.Protocol):
    """This uses djb's Netstrings protocol to break up the input into strings.
    
    Each string makes a callback to stringReceived, with a single argument of
    that string.
    """
    mode = LENGTH
    length = 0

    def stringReceived(self, line):
        """
        Override this.
        """
        raise NotImplementedError

    def doData(self):
        buffer,self.__data = self.__data[:self.length],self.__data[self.length:]
        self.length = self.length - len(buffer)
        self.__buffer = self.__buffer + buffer
        if self.length != 0:
            return
        self.stringReceived(self.__buffer)
        self.mode = COMMA

    def doComma(self):
        self.mode = LENGTH
        if self.__data[0] != ',':
            raise NetstringParseError(self.__data)
        self.__data = self.__data[1:]


    def doLength(self):
        m = NUMBER.match(self.__data)
        if not m.end():
            raise NetstringParseError(self.__data)
        self.__data = self.__data[m.end():]
        if m.group(1):
            self.length = self.length * (10**len(m.group(1))) + int(m.group(1))
        if m.group(2):
            self.__buffer = ''
            self.mode = DATA 
        
    def dataReceived(self, data):
        self.__data = data
        while self.__data:
            if self.mode == DATA:
                self.doData()
            elif self.mode == COMMA:
                self.doComma()
            elif self.mode == LENGTH:
                self.doLength()
            else:
                assert 0, "mode is not DATA, COMMA or LENGTH"

    def sendString(self, data):
        self.transport.write('%d:%s,' % (len(data), data))

class LineReceiver(protocol.Protocol):
    """A protocol which has a mode where it receives lines, and a mode where it receives raw data.
    
    Each line that's received becomes a callback to lineReceived.  Each chunk
    of raw data becomes a callback to rawDataReceived.

    This is useful for line-oriented protocols such as IRC, HTTP, POP, etc.
    """
    line_mode = 1
    buffer = ''
    delimiter = '\r\n'

    def dataReceived(self, data):
        """Protocol.dataReceived.
        Translates bytes into lines, and calls lineReceived (or
        rawDataReceived, depending on mode.)
        """
        self.buffer = self.buffer+data
        while self.line_mode:
            try:
                line, self.buffer = string.split(self.buffer, 
                                                   self.delimiter, 1)
            except ValueError:
                break
            else:
                self.lineReceived(line)
        else:
            data, self.buffer = self.buffer, ''
            if data:
                return self.rawDataReceived(data)

    def setLineMode(self, extra=''):
        """Sets the line-mode of this receiver.
        If you are calling this from a rawDataReceived callback, you can pass
        in extra unhandled data, and that data will be parsed for lines.
        Further data received will be sent to lineReceived rather than
        rawDataReceived.
        """
        self.line_mode = 1
        return self.dataReceived(extra)

    def setRawMode(self):
        """Sets the raw mode of this receiver.
        Further data received will be sent to rawDataReceived rather than lineReceived.
        """
        self.line_mode = 0

    def rawDataReceived(self, data):
        """Override this for when raw data is received.
        """
        raise NotImplementedError

    def lineReceived(self, line):
        """Override this for when each line is received.
        """
        raise NotImplementedError

    def sendLine(self, line):
        """Sends a line to the other end of the connection.
        """
        self.transport.write(line + self.delimiter)


class Int32StringReceiver(protocol.Protocol):
    """A receiver for int32-prefixed strings.
    
    This class is somewhat deprecated, but necessary for backwards
    compatibility for previous Gloop versions.  It publishes the same interface
    as NetstringReceiver.
    """

    recvd = ""

    def dataReceived(self, recd):
        """Convert int32 prefixed strings into calls to stringReceived.
        """
        packetList = []
        self.recvd = self.recvd + recd
        while len(self.recvd) > 3:
            length ,= struct.unpack("!i",self.recvd[:4])
            if len(self.recvd) < length+4:
                break
            packet = self.recvd[4:length+4]
            self.recvd = self.recvd[length+4:]
            self.stringReceived(packet)


    def sendString(self, data):
        """Send an int32-prefixed string to the other end of the connection.
        """
        self.transport.write(struct.pack("!i",len(data))+data)


class StatefulStringProtocol:
    """A stateful string protocol.
    
    This is a mixin for string protocols (Int32StringReceiver,
    NetstringReceiver) which translates stringReceived into a callback
    (prefixed with 'proto_') depending on state."""
    
    state = 'init'
    def stringReceived(self,string):
        """Choose a protocol phase function and call it.
        
        Call back to the appropriate protocol phase; this begins with
        the function proto_init and moves on to proto_* depending on
        what each proto_* function returns.  (For example, if
        self.proto_init returns 'foo', then self.proto_foo will be the
        next function called when a protocol message is received.
        """
        try:
            pto = 'proto_'+self.state
            statehandler = getattr(self,pto)
        except AttributeError:
            print 'callback',self.state,'not found'
        else:
            self.state = statehandler(string)
            if self.state == 'done':
                self.transport.loseConnection()

