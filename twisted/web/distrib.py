
# System Imports
import types, os, copy, string, cStringIO

# Twisted Imports
from twisted.spread import pb
from twisted.protocols import http
from twisted.internet import tcp

# Sibling Imports
import resource
import server
import error
import html
import static
from server import NOT_DONE_YET

class Request(pb.Copy, server.Request):
    def setCopiedState(self, state):
        pb.Copy.setCopiedState(self, state)
        # Emulate the local request interface --
        self.write            = self.remote.write
        self.finish           = self.remote.finish
        self.setHeader        = self.remote.setHeader
        self.setResponseCode  = self.remote.setResponseCode
        self.registerProducer = self.remote.registerProducer

pb.setCopierForClass(str(server.Request), Request)

class Issue:
    def __init__(self, request):
        self.request = request
        
    def finished(self, result):
        if result != NOT_DONE_YET:
            assert isinstance(result, types.StringType),\
                   "return value not a string"
            self.request.write(result)
            self.request.finish()

    def failed(self, expt):
        self.request.write(
            error.ErrorPage(http.INTERNAL_SERVER_ERROR,
                      "Server Connection Lost",
                      "Connection to distributed server lost:" +
                      html.PRE(expt)).
            render(self.request))


class ResourceSubscription(resource.Resource):
    isLeaf = 1
    waiting = 0
    def __init__(self, host, port, service="web", username="web", password="web"):
        resource.Resource.__init__(self)
        self.host = host
        self.port = port
        self.service = service
        self.username = username
        self.password = password
        self.pending = []
        self.publisher = None

    def __getstate__(self):
        state = copy.copy(self.__dict__)
        state['publisher'] = None
        state['waiting'] = 0
        return state

    def connected(self, publisher):
        """I've connected to a publisher; I'll now send all my requests.
        """
        print 'connected to publisher'
        self.publisher = publisher
        self.waiting = 0
        for request in self.pending:
            self.render(request)

    def notConnected(self):
        """I can't connect to a publisher; I'll now reply to all pending requests.
        """
        print "could not connect to distributed web service."
        for request in self.pending:
            request.write("Unable to connect to distributed server.")
            request.finish()

    def booted(self):
        print 'lost pb connection'
        self.waiting = 0
        self.publisher = None

    def render(self, request):
        """Render this request, from my server.
        
        This will always be asynchronous, and therefore return NOT_DONE_YET.
        It spins off a request to the pb client, and either adds it to the list
        of pending issues or requests it immediately, depending on if the
        client is already connected.
        """
        if not self.publisher:
            self.pending.append(request)
            if not self.waiting:
                broker = pb.Broker()
                broker.requestPerspective(self.service,
                                          self.username,
                                          self.password,
                                          self.connected,
                                          self.notConnected)
                broker.notifyOnDisconnect(self.booted)
                c = tcp.Client(self.host, self.port, broker)
                self.waiting = 1
        else:
            i = Issue(request)
            self.publisher.request(request,
                                   pbcallback=i.finished,
                                   pberrback=i.failed)
        return NOT_DONE_YET

class ResourcePublisher(pb.Service, pb.Perspective):
    def __init__(self, site):
        pb.Service.__init__(self)
        self.site = site
        
    def authenticate(self, user, challenge, password):
        return self

    def perspective_request(self, request):
        res = self.site.getResourceFor(request)
        print request
        return res.render(request)


class UserDirectory(html.Interface):
    userDirName = 'public_html'
    userSocketName = '.twistd-web-pb'

    def listUsers(self, req):
        import pwd
        x = cStringIO.StringIO()
        x.write('<UL>\n')
        for user in pwd.getpwall():
            pw_name, pw_passwd, pw_uid, pw_gid, pw_gecos, pw_dir, pw_shell \
                     = user
            realname = string.split(pw_gecos,',')[0]
            if not realname:
                realname = pw_name
            fmtStr = '<LI><A HREF="%s/">%s (%s)</a>\n'
            if os.path.exists(os.path.join(pw_dir, self.userDirName)):
                x.write(fmtStr% (req.childLink(pw_name),realname,'file'))
            twistdsock = os.path.join(pw_dir, self.userSocketName)
            if os.path.exists(twistdsock):
                linknm = '%s.twistd' % pw_name
                x.write(fmtStr% (req.childLink(linknm),realname,'twistd'))
        x.write('</UL>\n')
        return x.getvalue()

    def content(self, req):
        return "<CENTER>" + self.runBox(
            req,
            "Users with Homepages",
            self.listUsers, req) + "</CENTER>"

    def getChild(self, chnam, request):
        if chnam == '':
            return error.ErrorPage(http.NOT_FOUND,
                             "Bad URL",
                             "The empty string is not a valid user.")
        import pwd
        td = '.twistd'

        if chnam[-len(td):] == td:
            username = chnam[:-len(td)]
            sub = 1
        else:
            username = chnam
            sub = 0

        try:
            pw_name, pw_passwd, pw_uid, pw_gid, pw_gecos, pw_dir, pw_shell \
                     = pwd.getpwnam(username)
        except KeyError:
            return error.ErrorPage(http.NOT_FOUND,
                                   "No Such User",
                                   "The user %s was not found on this system." %
                                   repr(username))
        if sub:
            twistdsock = os.path.join(pw_dir, self.userSocketName)
            rs = ResourceSubscription('unix',twistdsock)
            self.putChild(chnam, rs)
            return rs
        else:
            return static.File(os.path.join(pw_dir, self.userDirName))
